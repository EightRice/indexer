# apps/trustless/paper.py

import json
import traceback
from datetime import datetime, timezone
from web3 import Web3
from google.cloud import firestore

from apps.trustless.abis import economyAbi, projectAbi, projectReadAbi
from apps.trustless.entities import Project, Backer, Transaction, Stage, ZERO_ADDRESS, User


class Paper:
    """
    Paper object handles events for a specific contract address.

    Kinds:
    - 'economy': Handles NewProject events from Economy contract
    - 'project': Handles all project lifecycle events from NativeProject/ERC20Project
    """

    def __init__(self, address: str, kind: str, web3: Web3, db,
                 network_collection: str, economy_address: str = None):
        self.address = Web3.to_checksum_address(address)
        self.kind = kind  # 'economy' or 'project'
        self.web3 = web3
        self.db = db
        self.network_collection = network_collection
        self.economy_address = economy_address  # For projects, which economy they belong to
        self.contract = None

        # Set ABI based on kind
        if kind == "economy":
            self.abi_string = economyAbi
        else:  # project
            self.abi_string = projectAbi

        if self.abi_string:
            self.abi = json.loads(self.abi_string)
        else:
            self.abi = None

    def get_contract(self):
        """Get or create contract instance"""
        if self.contract is None and self.address and self.abi:
            try:
                self.contract = self.web3.eth.contract(
                    address=self.address, abi=self.abi
                )
            except Exception as e:
                print(f"[trustless] Error creating contract for {self.address}: {e}")
                return None
        return self.contract

    def handle_event(self, log, func=None):
        """
        Route events to appropriate handlers.
        Returns new contract address(es) to listen to, or None.
        """
        if self.kind == 'economy':
            if func == 'NewProject':
                return self.handle_new_project(log)
        elif self.kind == 'project':
            handlers = {
                'SetParties': self.handle_set_parties,
                'SendFunds': self.handle_send_funds,
                'ImmediateFundsReleased': self.handle_immediate_funds_released,
                'ContractSigned': self.handle_contract_signed,
                'ProjectDisputed': self.handle_project_disputed,
                'ProjectClosed': self.handle_project_closed,
                'ArbitrationDecision': self.handle_arbitration_decision,
                'ArbitrationAppealed': self.handle_arbitration_appealed,
                'ArbitrationFinalized': self.handle_arbitration_finalized,
                'DaoOverruled': self.handle_dao_overruled,
                'ContractorPaid': self.handle_contractor_paid,
                'ContributorWithdrawn': self.handle_contributor_withdrawn,
                'AuthorPaid': self.handle_author_paid,
                'VetoedByDao': self.handle_vetoed_by_dao,
                'BackerVoteCast': self.handle_backer_vote_cast,
            }
            if func in handlers:
                try:
                    handlers[func](log)
                except Exception as e:
                    print(f"[trustless] Error handling {func} for {self.address}: {e}")
        return None

    def _get_project_ref(self):
        """Get Firestore reference to this project document"""
        return (self.db.collection(self.network_collection)
                .document(self.economy_address.lower())
                .collection("projects")
                .document(self.address.lower()))

    def _get_backers_collection(self):
        """Get Firestore reference to backers subcollection"""
        return self._get_project_ref().collection("backers")

    def _get_transactions_collection(self):
        """Get Firestore reference to transactions collection"""
        # For economy papers, use self.address; for project papers, use economy_address
        economy_addr = self.economy_address or self.address
        return (self.db.collection(self.network_collection)
                .document(economy_addr.lower())
                .collection("transactions"))


    def _get_users_collection(self):
        """Get Firestore reference to users subcollection"""
        economy_addr = self.economy_address or self.address
        return (self.db.collection(self.network_collection)
                .document(economy_addr.lower())
                .collection("users"))

    def _update_user(self, user_address: str, project_address: str, role: str, timestamp: datetime):
        """
        Update user document with project involvement.
        Uses arrayUnion for idempotency.
        role: 'authored', 'contracted', 'arbitrated', or 'backed'
        """
        user_addr = user_address.lower()
        project_addr = project_address.lower()
        
        field_map = {
            'authored': 'projectsAuthored',
            'contracted': 'projectsContracted',
            'arbitrated': 'projectsArbitrated',
            'backed': 'projectsBacked',
        }
        
        field_name = field_map.get(role)
        if not field_name:
            print(f"[trustless] Unknown user role: {role}")
            return
        
        user_ref = self._get_users_collection().document(user_addr)
        user_ref.set({
            'lastActive': timestamp,
            field_name: firestore.ArrayUnion([project_addr])
        }, merge=True)
        
        print(f"[trustless] Updated user {user_addr[:10]}... as {role} for project {project_addr[:10]}...")

    def _record_transaction(self, log, function_name: str, params: dict = None, project_id: str = None):
        """Record a transaction in Firestore"""
        tx_hash = log['transactionHash'].hex()
        log_index = log.get('logIndex', 0)
        block = self.web3.eth.get_block(log['blockNumber'])

        tx = Transaction(
            txHash=tx_hash,
            sender=self._get_tx_sender(log),
            functionName=function_name,
            contractAddress=self.economy_address or self.address,
            projectId=project_id or self.address,
            params=params or {},
            time=datetime.fromtimestamp(block['timestamp'], tz=timezone.utc),
            blockNumber=log['blockNumber'],
        )

        # Use tx_hash + log_index as document ID to handle multiple events per transaction
        doc_id = f"{tx_hash}_{log_index}"
        self._get_transactions_collection().document(doc_id).set(tx.to_firestore())
        print(f"[trustless] Recorded transaction: {function_name} - {tx_hash[:10]}...")

    def _get_tx_sender(self, log) -> str:
        """Get the sender address from a transaction"""
        try:
            tx = self.web3.eth.get_transaction(log['transactionHash'])
            return tx['from'].lower()
        except Exception as e:
            print(f"[trustless] Error getting tx sender: {e}")
            return ""

    def _update_amount_from_onchain(self):
        """Read actual balance from on-chain and update Firestore amount field"""
        try:
            read_abi = json.loads(projectReadAbi)
            read_contract = self.web3.eth.contract(address=self.address, abi=read_abi)

            # Check if this is an ERC20 project by reading token address
            token_address = read_contract.functions.token().call()

            if token_address and token_address != ZERO_ADDRESS:
                # ERC20 project - get token balance of project contract
                erc20_abi = json.loads('[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]')
                token_contract = self.web3.eth.contract(address=token_address, abi=erc20_abi)
                on_chain_amount = str(token_contract.functions.balanceOf(self.address).call())
            else:
                # Native project - get ETH balance of project contract
                on_chain_amount = str(self.web3.eth.get_balance(self.address))

            # Update project amount from on-chain data
            self._get_project_ref().update({'amount': on_chain_amount})
            print(f'[trustless] Updated project amount to {on_chain_amount} from on-chain balance')
            return on_chain_amount
        except Exception as e:
            print(f'[trustless] Warning: Could not read/update balance: {e}')
            return None

    # ============ Economy Event Handlers ============

    def handle_new_project(self, log):
        """Handle NewProject event - creates project document and returns address to listen to"""
        contract = self.get_contract()
        if not contract:
            return None

        try:
            decoded = contract.events.NewProject().process_log(log)
            args = decoded['args']

            project_address = args['contractAddress']
            project_name = args['projectName']

            print(f"[trustless] NewProject: {project_name} at {project_address}")

            # Get block timestamp
            block = self.web3.eth.get_block(log['blockNumber'])
            created_at = datetime.fromtimestamp(block['timestamp'], tz=timezone.utc)

            # Get transaction sender (author)
            author = self._get_tx_sender(log) or ""

            # Get addresses with fallback to empty string if None
            contractor = args.get('contractor') or ""
            arbiter = args.get('arbiter') or ""
            token_address = args.get('token') or ""

            # Create project entity
            project = Project(
                address=project_address,
                name=project_name,
                description=args.get('description') or "",
                repo=args.get('repo') or "",
                termsHash=args.get('termsHash') or "",
                author=author,
                contractor=contractor,
                arbiter=arbiter,
                tokenAddress=token_address,
                stage=Stage.OPEN,
                networkName=self.network_collection,
                economyAddress=self.address,
                createdAt=created_at,
            )

            # Write to Firestore
            project_ref = (self.db.collection(self.network_collection)
                          .document(self.address.lower())
                          .collection("projects")
                          .document(project_address.lower()))
            project_ref.set(project.to_firestore())

            # Record transaction
            self._record_transaction(log, "createProject", {
                "name": project_name,
                "contractor": contractor,
                "arbiter": arbiter,
                "tokenAddress": token_address,
            }, project_id=project_address)

            print(f"[trustless] Created project document: {project_address}")

            # Update author's user profile
            if author:
                self._update_user(author, project_address, 'authored', created_at)

            # Return the new project address so it gets added to listeners
            return project_address

        except Exception as e:
            print(f"[trustless] Error processing NewProject: {e}")
            traceback.print_exc()
            return None

    # ============ Project Event Handlers ============

    def handle_set_parties(self, log):
        """Handle SetParties - updates contractor, arbiter, termsHash, stage"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.SetParties().process_log(log)
        args = decoded['args']

        contractor = args['_contractor'].lower()
        arbiter = args['_arbiter'].lower()

        updates = {
            "contractor": contractor,
            "arbiter": arbiter,
            "termsHash": args['_termsHash'],
            "stage": Stage.PENDING,
        }

        self._get_project_ref().update(updates)
        self._record_transaction(log, "setParties", {
            "contractor": args['_contractor'],
            "arbiter": args['_arbiter'],
            "termsHash": args['_termsHash'],
        })

        # Get block timestamp for lastActive
        block = self.web3.eth.get_block(log['blockNumber'])
        timestamp = datetime.fromtimestamp(block['timestamp'], tz=timezone.utc)

        # Update user profiles for contractor and arbiter
        if contractor and contractor != ZERO_ADDRESS:
            self._update_user(contractor, self.address, 'contracted', timestamp)
        if arbiter and arbiter != ZERO_ADDRESS:
            self._update_user(arbiter, self.address, 'arbitrated', timestamp)

        print(f"[trustless] SetParties: contractor={args['_contractor'][:10]}...")

    def handle_send_funds(self, log):
        """Handle SendFunds - update project amount, upsert backer"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.SendFunds().process_log(log)
        args = decoded['args']

        who = args['who'].lower()
        how_much = str(args['howMuch'])
        immediate_bps = args['immediateBps']

        # Update project amount
        project_ref = self._get_project_ref()

        @firestore.transactional
        def update_in_transaction(transaction):
            doc = project_ref.get(transaction=transaction)
            if doc.exists:
                current_amount = int(doc.to_dict().get('amount', '0'))
                new_amount = current_amount + int(how_much)
                transaction.update(project_ref, {'amount': str(new_amount)})

        transaction = self.db.transaction()
        update_in_transaction(transaction)

        # Upsert backer
        backer_ref = self._get_backers_collection().document(who)
        backer_doc = backer_ref.get()

        if backer_doc.exists:
            # Update existing backer - weighted average for immediateBps
            existing = backer_doc.to_dict()
            existing_contribution = int(existing.get('contribution', '0'))
            existing_bps = existing.get('immediateBps', 0)

            new_contribution = existing_contribution + int(how_much)
            # Weighted average of immediateBps
            if new_contribution > 0:
                new_bps = ((existing_contribution * existing_bps) +
                          (int(how_much) * immediate_bps)) // new_contribution
            else:
                new_bps = immediate_bps

            backer_ref.update({
                'contribution': str(new_contribution),
                'immediateBps': new_bps,
                'lastUpdated': datetime.now(timezone.utc),
            })
        else:
            # Create new backer
            backer = Backer(
                address=who,
                contribution=how_much,
                immediateBps=immediate_bps,
            )
            backer_ref.set(backer.to_firestore())

        self._record_transaction(log, "sendFunds", {
            "amount": how_much,
            "immediateBps": immediate_bps,
        })

        # Get block timestamp for lastActive
        block = self.web3.eth.get_block(log['blockNumber'])
        timestamp = datetime.fromtimestamp(block['timestamp'], tz=timezone.utc)

        # Update user profile for backer
        self._update_user(who, self.address, 'backed', timestamp)

        print(f"[trustless] SendFunds: {who[:10]}... sent {how_much} wei")

    def handle_immediate_funds_released(self, log):
        """Handle ImmediateFundsReleased - funds sent to contractor, update balance"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ImmediateFundsReleased().process_log(log)
        args = decoded['args']

        amount = str(args['amount'])

        self._get_project_ref().update({
            'immediateReleased': amount,
        })

        # Update project amount from on-chain balance (funds left the contract)
        self._update_amount_from_onchain()

        self._record_transaction(log, "immediateFundsReleased", {
            "contractor": args['contractor'],
            "amount": amount,
        })

        print(f"[trustless] ImmediateFundsReleased: {amount} wei")

    def handle_contract_signed(self, log):
        """Handle ContractSigned - stage becomes ongoing"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ContractSigned().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.ONGOING,
        })

        self._record_transaction(log, "signContract", {
            "contractor": args['contractor'],
        })

        print(f"[trustless] ContractSigned: stage -> ongoing")

    def handle_project_disputed(self, log):
        """Handle ProjectDisputed - stage becomes dispute"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ProjectDisputed().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.DISPUTE,
        })

        self._record_transaction(log, "projectDisputed", {
            "by": args['by'],
        })

        print(f"[trustless] ProjectDisputed: stage -> dispute")

    def handle_project_closed(self, log):
        """Handle ProjectClosed - stage becomes closed"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ProjectClosed().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.CLOSED,
        })

        self._record_transaction(log, "projectClosed", {
            "by": args['by'],
        })

        print(f"[trustless] ProjectClosed: stage -> closed")

    def handle_arbitration_decision(self, log):
        """Handle ArbitrationDecision - stage becomes appealable"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ArbitrationDecision().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.APPEALABLE,
            'disputeResolution': args['percent'],
            'rulingHash': args['rulingHash'],
        })

        self._record_transaction(log, "arbitrate", {
            "arbiter": args['arbiter'],
            "percent": args['percent'],
            "rulingHash": args['rulingHash'],
        })

        print(f"[trustless] ArbitrationDecision: {args['percent']}% -> appealable")

    def handle_arbitration_appealed(self, log):
        """Handle ArbitrationAppealed - stage becomes appeal"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ArbitrationAppealed().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.APPEAL,
            'appealProposalId': str(args['proposalId']),
        })

        self._record_transaction(log, "appeal", {
            "appealer": args['appealer'],
            "proposalId": str(args['proposalId']),
        })

        print(f"[trustless] ArbitrationAppealed: proposal {args['proposalId']}")

    def handle_arbitration_finalized(self, log):
        """Handle ArbitrationFinalized - stage becomes closed"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ArbitrationFinalized().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.CLOSED,
        })

        self._record_transaction(log, "finalizeArbitration", {
            "finalizer": args['finalizer'],
        })

        print(f"[trustless] ArbitrationFinalized: stage -> closed")

    def handle_dao_overruled(self, log):
        """Handle DaoOverruled - DAO timelock overrides arbiter decision"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.DaoOverruled().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.CLOSED,
            'disputeResolution': args['percent'],
            'rulingHash': args['rulingHash'],
        })

        self._record_transaction(log, "daoOverruled", {
            "timelock": args['timelock'],
            "percent": args['percent'],
            "rulingHash": args['rulingHash'],
        })

        print(f"[trustless] DaoOverruled: {args['percent']}%")

    def handle_contractor_paid(self, log):
        """Handle ContractorPaid - funds sent to contractor, update balance"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ContractorPaid().process_log(log)
        args = decoded['args']

        # Update project amount from on-chain balance (funds left the contract)
        self._update_amount_from_onchain()

        self._record_transaction(log, "withdrawAsContractor", {
            "contractor": args['contractor'],
            "amount": str(args['amount']),
        })

        print(f"[trustless] ContractorPaid: {args['amount']} wei")

    def handle_contributor_withdrawn(self, log):
        """Handle ContributorWithdrawn - delete backer and update project amount from on-chain"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.ContributorWithdrawn().process_log(log)
        args = decoded['args']

        contributor = args['contributor'].lower()
        withdrawn_amount = str(args['amount'])

        # Update project amount from on-chain balance (funds left the contract)
        self._update_amount_from_onchain()

        # Delete backer document
        self._get_backers_collection().document(contributor).delete()

        self._record_transaction(log, "withdrawAsContributor", {
            "contributor": contributor,
            "amount": withdrawn_amount,
        })

        print(f"[trustless] ContributorWithdrawn: {contributor[:10]}... withdrew {withdrawn_amount} wei")

    def handle_author_paid(self, log):
        """Handle AuthorPaid"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.AuthorPaid().process_log(log)
        args = decoded['args']

        self._record_transaction(log, "authorPaid", {
            "author": args['author'],
            "amount": str(args['amount']),
        })

        print(f"[trustless] AuthorPaid: {args['amount']} wei")

    def handle_vetoed_by_dao(self, log):
        """Handle VetoedByDao - project is closed with 0% to contractor"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.VetoedByDao().process_log(log)
        args = decoded['args']

        self._get_project_ref().update({
            'stage': Stage.CLOSED,
            'disputeResolution': 0,
        })

        self._record_transaction(log, "vetoedByDao", {
            "timelock": args['timelock'],
        })

        print(f"[trustless] VetoedByDao: stage -> closed (0% to contractor)")

    def handle_backer_vote_cast(self, log):
        """Handle BackerVoteCast - record backer's vote for/against fund release"""
        contract = self.get_contract()
        if not contract:
            return

        decoded = contract.events.BackerVoteCast().process_log(log)
        args = decoded['args']

        voter = args['voter'].lower()
        for_dispute = args['forDispute']
        voting_power = str(args['votingPower'])

        # Update backer's vote in Firestore
        # vote field expects: "none", "release", or "dispute"
        vote_value = "dispute" if for_dispute else "release"
        backer_ref = self._get_backers_collection().document(voter)
        backer_ref.set({
            'vote': vote_value,
            'votingPower': voting_power,
        }, merge=True)

        self._record_transaction(log, "voteToDispute" if for_dispute else "voteToRelease", {
            "voter": voter,
            "forDispute": for_dispute,
            "votingPower": voting_power,
        })

        vote_type = "dispute" if for_dispute else "release"
        print(f"[trustless] BackerVoteCast: {voter[:10]}... voted for {vote_type} with {voting_power} power")


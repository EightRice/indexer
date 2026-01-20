# apps/homebase/paper.py

from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal, wrapper_token_abi, timelock_min_delay_abi, wrapper_w_abi, trustless_wrapper_abi, registryAbi
from datetime import datetime, timezone, timedelta
from apps.homebase.entities import ProposalStatus, Proposal, StateInContract, Txaction, Token, Member, Org, Vote
import re
from web3 import Web3
from google.cloud import firestore
import codecs
from apps.generic.converting import decode_function_parameters
from apps.homebase.eventSignatures import quorum_function_abi, voting_period_function_abi,proposal_threshold_function_abi, voting_delay_function_abi, mint_function_abi, burn_function_abi

class Paper:
    ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
    def __init__(self, address, kind, web3, daos_collection, db, dao=None, token=None,
                 trustless_db=None, trustless_network_collection=None):
        self.address = address
        self.kind = kind
        self.contract = None
        self.dao = dao
        self.token_paper: Paper = token
        self.web3: Web3 = web3
        self.daos_collection = daos_collection
        self.db = db
        self.abi_string = None
        # Trustless cross-write support (only used by wrapper_trustless)
        self.trustless_db = trustless_db
        self.trustless_network_collection = trustless_network_collection

        if kind == "wrapper":
            self.abi_string = wrapperAbi
        elif kind == "wrapper_t":
            # Transferable non-wrapped token factory (emits NewDaoCreated)
            self.abi_string = wrapperAbi
        elif kind == "wrapper_w":
            # Wrapped ERC20 token factory (emits DaoWrappedDeploymentInfo)
            # wrapper_w_abi contains both NewDaoCreated and DaoWrappedDeploymentInfo for compatibility
            self.abi_string = wrapper_w_abi
        elif kind == "wrapper_trustless":
            # TrustlessFactory emits NewDaoCreated and SuiteConfigured events
            self.abi_string = trustless_wrapper_abi
        elif kind == "registry":
            # Registry contract for Economy DAOs - emits RegistryUpdated events
            self.abi_string = registryAbi
        elif kind == "token":
            self.abi_string = tokenAbiGlobal
        else: # dao
            self.abi_string = daoAbiGlobal
        
        if self.abi_string:
            self.abi = re.sub(r'\n+', ' ', self.abi_string).strip()
        else:
            self.abi = None


    def get_contract(self):
        if self.contract is None and self.address and self.abi:
            try:
                self.contract = self.web3.eth.contract(
                    address=Web3.to_checksum_address(self.address), abi=self.abi)
            except Exception as e:
                print(f"Error creating contract object for {self.address} with kind {self.kind}: {e}")
                return None
        return self.contract

    def get_specific_contract(self, address, abi):
        """Helper to get a contract instance with a specific address and ABI."""
        try:
            final_abi = abi
            if isinstance(abi, str):
                final_abi = re.sub(r'\n+', ' ', abi).strip()
            
            return self.web3.eth.contract(
                address=Web3.to_checksum_address(address), abi=final_abi
            )
        except Exception as e:
            print(f"Error creating specific contract {address}: {e}")
            return None

    def add_dao(self, log):
        contract_instance = self.get_contract()
        if not contract_instance:
            print(f"Could not get contract instance for {self.address} in add_dao")
            return None
        try:
            decoded_event = contract_instance.events.NewDaoCreated().process_log(log)
        except Exception as e:
            print(f"Error processing NewDaoCreated log with ABI for {self.address}: {e}")
            return None

        args = decoded_event['args']
        name = args['name']
        safe_name = name.encode('ascii', 'replace').decode()
        print(f"New DAO (original wrapper): {safe_name} from event")
        
        org = Org(name=name)
        org.creationDate = datetime.now(timezone.utc)
        org.govTokenAddress = args['token']
        org.address = args['dao']
        org.symbol = args['symbol']
        org.registryAddress = args['registry']
        org.description = args['description']
        members = args['initialMembers']
        amounts = args['initialAmounts']
        org.holders = len(members) if members else 0

        token_contract = self.get_specific_contract(org.govTokenAddress, tokenAbiGlobal)
        if token_contract:
            try:
                org.decimals = token_contract.functions.decimals().call()
            except Exception as e:
                print(f"Error fetching decimals for token {org.govTokenAddress}: {e}")
                org.decimals = 18

            # Fetch transferability status from token contract
            try:
                is_transferable = token_contract.functions.isTransferable().call()
                # Note: nonTransferrable is the OPPOSITE of isTransferable
                org.nonTransferrable = not is_transferable
                print(f"Token {org.govTokenAddress} isTransferable: {is_transferable}, nonTransferrable: {org.nonTransferrable}")
            except Exception as e:
                print(f"Error fetching isTransferable for token {org.govTokenAddress}: {e}")
                org.nonTransferrable = False  # Default to transferable if we can't read it
        else:
            org.decimals = 18
            org.nonTransferrable = False
        
        # --- START OF FIX ---
        # The previous logic incorrectly tried to read settings from the 'initialAmounts' array.
        # The correct method is to query the newly created DAO contract for its actual settings.
        dao_contract = self.get_specific_contract(org.address, daoAbiGlobal)
        if dao_contract:
            try:
                # Fetch settings directly from the DAO contract.
                # As you noted, the contract returns these values in seconds.
                # We convert them to minutes for storage in Firestore.
                voting_delay_seconds = dao_contract.functions.votingDelay().call()
                org.votingDelay = voting_delay_seconds // 60

                voting_period_seconds = dao_contract.functions.votingPeriod().call()
                org.votingDuration = voting_period_seconds // 60

                org.proposalThreshold = str(dao_contract.functions.proposalThreshold().call())
                
                # The OZ Governor contract has a quorumNumerator function we can use.
                org.quorum = dao_contract.functions.quorumNumerator().call()
                
                print(f"Successfully fetched on-chain settings for DAO {name}.")

            except Exception as e:
                print(f"ERROR: Could not fetch on-chain settings for new DAO {org.address}: {e}. Defaulting to 0.")
                org.votingDelay = 0
                org.votingDuration = 0
                org.proposalThreshold = "0"
                org.quorum = 0
        else:
            print(f"WARNING: Could not create contract instance for new DAO {org.address}. Settings will be defaulted to 0.")
            org.votingDelay = 0
            org.votingDuration = 0
            org.proposalThreshold = "0"
            org.quorum = 0
        
        # executionDelay is correct as it's a dedicated field in the event.
        org.executionDelay = args['executionDelay']
        # --- END OF FIX ---

        supply = 0
        batch = self.db.batch()
        for i in range(len(members)):
            member_address_checksum = Web3.to_checksum_address(members[i])
            member_balance = amounts[i]
            supply += member_balance
            m = Member(address=member_address_checksum, personalBalance=str(member_balance), delegate="", votingWeight="0")
            member_doc_ref = self.daos_collection.document(org.address).collection('members').document(m.address)
            batch.set(member_doc_ref, m.toJson())
        
        org.totalSupply = str(supply)

        keys = args['keys']
        values = args['values']
        if keys and values and len(keys) == len(values):
            org.registry = {keys[i]: values[i] for i in range(len(keys)) if keys[i] and values[i]}
        else:
            org.registry = {}
        
        # Check if there's a pending economy address from SuiteConfigured event
        # (SuiteConfigured may arrive before NewDaoCreated in the same transaction)
        if hasattr(self, '_pending_economy_addresses') and org.address in self._pending_economy_addresses:
            org.economy = self._pending_economy_addresses[org.address]
            del self._pending_economy_addresses[org.address]
            print(f"Applied pending economy address {org.economy} to DAO {org.address}")
        else:
            # Check if document already exists with economy field (SuiteConfigured may have updated it)
            existing_doc = self.daos_collection.document(org.address).get()
            if existing_doc.exists:
                existing_data = existing_doc.to_dict()
                if existing_data.get('economy'):
                    org.economy = existing_data['economy']
                    print(f"Preserved existing economy address {org.economy} for DAO {org.address}")

        # Check if there's a pending description from SuiteConfigured event
        # (For Economy DAOs, description is fetched from Registry during SuiteConfigured)
        if hasattr(self, '_pending_descriptions') and org.address in self._pending_descriptions:
            org.description = self._pending_descriptions[org.address]
            del self._pending_descriptions[org.address]
            print(f"Applied pending description to DAO {org.address}: {org.description[:50]}...")

        # Debug: print economy value before serialization
        dao_json = org.toJson()
        print(f"[DEBUG] org.economy value: {org.economy}, type: {type(org.economy)}")
        print(f"[DEBUG] dao_json economy: {dao_json.get('economy')}, type: {type(dao_json.get('economy'))}")
        
        # Force economy to be a string if it exists
        if org.economy:
            dao_json['economy'] = str(org.economy)
            print(f"[DEBUG] Forced economy to string: {dao_json['economy']}")
        
        # Use set with explicit data to ensure economy is written
        doc_ref = self.daos_collection.document(org.address)
        
        # Make a copy to prevent any mutation issues
        import copy
        dao_json_copy = copy.deepcopy(dao_json)
        print(f"[DEBUG] About to call set() with economy: {dao_json_copy.get('economy')}")
        
        doc_ref.set(dao_json_copy)
        
        print(f"[DEBUG] After set(), dao_json_copy economy: {dao_json_copy.get('economy')}")
        
        # Verify what was written
        verify_doc = doc_ref.get()
        if verify_doc.exists:
            verify_data = verify_doc.to_dict()
            print(f"[DEBUG] Verified economy in Firestore: {verify_data.get('economy')}")
        
        try:
            batch.commit()
            print(f"Successfully added DAO {org.name} / {org.address} to Firestore.")
        except Exception as e:
            print(f"Error committing batch for DAO {org.name}: {e}")

        return [org.address, org.govTokenAddress]


    def add_dao_wrapped(self, log):
        contract_instance = self.get_contract()
        if not contract_instance:
            print(f"Could not get contract instance for {self.address} in add_dao_wrapped")
            return None
        try:
            decoded_event = contract_instance.events.DaoWrappedDeploymentInfo().process_log(log)
        except Exception as e:
            print(f"Error processing DaoWrappedDeploymentInfo log with ABI for {self.address}: {e}")
            return None

        args = decoded_event['args']
        dao_name = args['daoName']
        print(f"New DAO (wrapped wrapper): {dao_name} from event")
        org = Org(name=dao_name)
        org.creationDate = datetime.now(timezone.utc)
        org.govTokenAddress = args['wrappedTokenAddress']
        org.address = args['daoAddress']
        org.symbol = args['wrappedTokenSymbol']
        org.registryAddress = args['registryAddress']
        org.description = args['description']
        org.quorum = args['quorumFraction']

        # Set underlyingToken from event (new StandardFactoryWrapped includes it)
        if 'underlyingTokenAddress' in args:
            org.underlyingToken = str(args['underlyingTokenAddress'])
            print(f"Underlying token from event: {org.underlyingToken}")

        # Get governance settings from event (new StandardFactoryWrapped includes these)
        if 'executionDelay' in args:
            org.executionDelay = args['executionDelay']
        if 'votingDelay' in args:
            # Event provides values in seconds, convert to minutes for DB
            org.votingDelay = args['votingDelay'] // 60
        if 'votingPeriod' in args:
            # Event provides values in seconds, convert to minutes for DB
            org.votingDuration = args['votingPeriod'] // 60
        if 'proposalThreshold' in args:
            org.proposalThreshold = str(args['proposalThreshold'])

        org.holders = 0

        wrapped_token_contract = self.get_specific_contract(org.govTokenAddress, wrapper_token_abi)

        if wrapped_token_contract:
            try:
                org.decimals = wrapped_token_contract.functions.decimals().call()
                org.totalSupply = str(wrapped_token_contract.functions.totalSupply().call())
                # Also fetch from contract as backup (sets both fields for web app)
                underlying_from_contract = str(wrapped_token_contract.functions.underlying().call())
                if not org.underlyingToken:
                    org.underlyingToken = underlying_from_contract
                print(f"Wrapped token info: decimals={org.decimals}, totalSupply={org.totalSupply}, underlying={org.underlyingToken}")
            except Exception as e:
                print(f"Error fetching info for wrapped token {org.govTokenAddress}: {e}")
                if not hasattr(org, 'decimals') or org.decimals is None:
                    org.decimals = 18
                if not hasattr(org, 'totalSupply') or org.totalSupply is None:
                    org.totalSupply = "0"

            # Fetch transferability status from wrapped token contract
            try:
                is_transferable = wrapped_token_contract.functions.isTransferable().call()
                # Note: nonTransferrable is the OPPOSITE of isTransferable
                org.nonTransferrable = not is_transferable
                print(f"Wrapped token {org.govTokenAddress} isTransferable: {is_transferable}, nonTransferrable: {org.nonTransferrable}")
            except Exception as e:
                print(f"Error fetching isTransferable for wrapped token {org.govTokenAddress}: {e}")
                org.nonTransferrable = False  # Default to transferable if we can't read it
        else:
            print(f"Could not create contract instance for wrapped token {org.govTokenAddress}")
            if not hasattr(org, 'decimals') or org.decimals is None:
                org.decimals = 18
            if not hasattr(org, 'totalSupply') or org.totalSupply is None:
                org.totalSupply = "0"
            org.nonTransferrable = False

        # Fallback: If governance settings weren't in the event, query contracts
        if not hasattr(org, 'proposalThreshold') or org.proposalThreshold is None:
            dao_contract = self.get_specific_contract(org.address, daoAbiGlobal)
            if dao_contract:
                try:
                    org.proposalThreshold = str(dao_contract.functions.proposalThreshold().call())
                    org.votingDelay = dao_contract.functions.votingDelay().call() // 60
                    org.votingDuration = dao_contract.functions.votingPeriod().call() // 60

                    timelock_address = dao_contract.functions.timelock().call()
                    timelock_contract = self.get_specific_contract(timelock_address, [timelock_min_delay_abi])
                    if timelock_contract:
                        org.executionDelay = timelock_contract.functions.getMinDelay().call()
                    print(f"Fetched governance settings from contracts as fallback")
                except Exception as e:
                    print(f"Error fetching DAO/Timelock settings for {org.address}: {e}")
                    org.proposalThreshold = "0"
                    org.votingDelay = 0
                    org.votingDuration = 0
                    org.executionDelay = 0
            else:
                print(f"Could not create DAO contract instance, using defaults")
                org.proposalThreshold = "0"
                org.votingDelay = 0
                org.votingDuration = 0
                org.executionDelay = 0

        # Ensure all required fields have defaults
        if not hasattr(org, 'proposalThreshold') or org.proposalThreshold is None:
            org.proposalThreshold = "0"
        if not hasattr(org, 'votingDelay') or org.votingDelay is None:
            org.votingDelay = 0
        if not hasattr(org, 'votingDuration') or org.votingDuration is None:
            org.votingDuration = 0
        if not hasattr(org, 'executionDelay') or org.executionDelay is None:
            org.executionDelay = 0

        org.registry = {}

        # Debug: print economy value before serialization
        dao_json = org.toJson()
        print(f"[DEBUG] DAO toJson economy field: {dao_json.get('economy')}")
        self.daos_collection.document(org.address).set(dao_json)
        print(f"Successfully added DAO (wrapped) {org.name} / {org.address} to Firestore.")
        return [org.address, org.govTokenAddress]


    def configure_economy(self, log):
        """
        Handle SuiteConfigured event from TrustlessFactory.
        Updates the DAO document with the economy contract address.
        Also creates an Economy document in the Trustless Firestore.

        Event: SuiteConfigured(deployer, economy, registry, timelock, repToken, dao)

        Note: SuiteConfigured may arrive before NewDaoCreated in the same transaction
        since events are processed in log order. We store pending economy addresses
        to be applied when the DAO is created.
        """
        contract_instance = self.get_contract()
        if not contract_instance:
            print(f"Could not get contract instance for {self.address} in configure_economy")
            return None

        try:
            decoded_event = contract_instance.events.SuiteConfigured().process_log(log)
        except Exception as e:
            print(f"Error processing SuiteConfigured log for {self.address}: {e}")
            return None

        args = decoded_event['args']
        deployer_address = Web3.to_checksum_address(args['deployer'])
        dao_address = Web3.to_checksum_address(args['dao'])
        economy_address = Web3.to_checksum_address(args['economy'])
        registry_address = Web3.to_checksum_address(args['registry'])
        timelock_address = Web3.to_checksum_address(args['timelock'])
        rep_token_address = Web3.to_checksum_address(args['repToken'])

        print(f"SuiteConfigured: DAO {dao_address} linked to Economy {economy_address}")

        # Query the registry contract for the description (set during deployment)
        # This is more reliable than trying to catch the RegistryUpdated event from the same tx
        description = None
        try:
            registry_contract = self.get_specific_contract(registry_address, registryAbi)
            if registry_contract:
                description = registry_contract.functions.getRegistryValue("description").call()
                if description:
                    print(f"Fetched description from Registry {registry_address}: {description[:50]}...")
                else:
                    print(f"No description found in Registry {registry_address}")
        except Exception as e:
            print(f"Error fetching description from Registry {registry_address}: {e}")

        # Update the DAO document with economy address (Homebase Firestore)
        try:
            dao_doc_ref = self.daos_collection.document(dao_address)
            dao_doc = dao_doc_ref.get()

            if dao_doc.exists:
                update_data = {'economy': economy_address}
                if description:
                    update_data['description'] = description
                    update_data['registry.description'] = description
                dao_doc_ref.update(update_data)
                print(f"Updated DAO {dao_address} with economy address {economy_address}")
            else:
                # DAO doesn't exist yet - store pending economy address and description
                # This happens when SuiteConfigured arrives before NewDaoCreated
                if not hasattr(self, '_pending_economy_addresses'):
                    self._pending_economy_addresses = {}
                if not hasattr(self, '_pending_descriptions'):
                    self._pending_descriptions = {}
                self._pending_economy_addresses[dao_address] = economy_address
                if description:
                    self._pending_descriptions[dao_address] = description
                print(f"Stored pending economy address for DAO {dao_address}: {economy_address}")
        except Exception as e:
            print(f"Error updating DAO {dao_address} with economy address: {e}")

        # Cross-write Economy document to Trustless Firestore
        if self.trustless_db and self.trustless_network_collection:
            try:
                # Get block timestamp for createdAt
                block_number = log.get('blockNumber')
                block = self.web3.eth.get_block(block_number)
                created_at = datetime.fromtimestamp(block['timestamp'], tz=timezone.utc)

                # Create Economy document in Trustless Firestore
                # Structure: {network_collection}/{economy_address}
                # Use lowercase address for document ID to match Flutter app lookup
                economy_address_lower = economy_address.lower()
                economy_doc_ref = self.trustless_db.collection(self.trustless_network_collection).document(economy_address_lower)

                economy_data = {
                    'address': economy_address_lower,
                    'daoAddress': dao_address.lower(),
                    'registryAddress': registry_address.lower(),
                    'timelockAddress': timelock_address.lower(),
                    'repTokenAddress': rep_token_address.lower(),
                    'creator': deployer_address.lower(),
                    'createdAt': created_at,
                    'createdAtBlock': block_number,
                }

                economy_doc_ref.set(economy_data)
                print(f"Created Economy document in Trustless Firestore: {economy_address_lower}")

            except Exception as e:
                print(f"Error creating Economy document in Trustless Firestore: {e}")
                import traceback
                traceback.print_exc()
        else:
            if not self.trustless_db:
                print("Trustless DB not configured - skipping cross-write")

        # Return economy and registry addresses with marker so main loop can add them to listener
        # Registry paper is needed to capture RegistryUpdated events for description updates
        return [('economy', economy_address), ('registry', registry_address, dao_address)]


    def delegate(self, log):
        if not self.dao:
            print(f"DAO address not set for token {self.address}, cannot process delegate event.")
            return None
            
        # Handle both raw logs (from real-time) and processed events (from get_logs)
        if 'args' in log:
            # Already processed event from get_logs()
            data = log
        else:
            # Raw log from real-time listener, needs processing
            contract_instance = self.get_contract()
            if not contract_instance: return None
            try:
                data = contract_instance.events.DelegateChanged().process_log(log)
            except Exception as e:
                print(f"Error processing DelegateChanged for {self.address} in DAO {self.dao}: {e}")
                return None

        args = data['args']
        delegator = Web3.to_checksum_address(args['delegator'])
        from_delegate = Web3.to_checksum_address(args['fromDelegate'])
        to_delegate = Web3.to_checksum_address(args['toDelegate'])
        
        batch = self.db.batch()
        delegator_member_ref = self.daos_collection.document(self.dao).collection('members').document(delegator)
        
        delegator_doc = delegator_member_ref.get()
        if not delegator_doc.exists:
            print(f"Delegator {delegator} not found as member in DAO {self.dao}. Creating.")
            try:
                token_contract_instance = self.get_contract()
                balance = token_contract_instance.functions.balanceOf(delegator).call()
                new_member = Member(address=delegator, personalBalance=str(balance), delegate=to_delegate, votingWeight="0")
                batch.set(delegator_member_ref, new_member.toJson())
            except Exception as e:
                print(f"Error creating new member {delegator} for delegation: {e}")
                new_member = Member(address=delegator, personalBalance="0", delegate=to_delegate, votingWeight="0")
                batch.set(delegator_member_ref, new_member.toJson())

        else:
            batch.update(delegator_member_ref, {"delegate": to_delegate})

        if to_delegate != self.ZERO_ADDRESS and to_delegate != delegator:
            to_delegate_member_ref = self.daos_collection.document(self.dao).collection('members').document(to_delegate)
            to_delegate_doc = to_delegate_member_ref.get()
            if not to_delegate_doc.exists:
                print(f"Delegatee {to_delegate} not found as member in DAO {self.dao}. Creating.")
                try:
                    token_contract_instance = self.get_contract()
                    balance = token_contract_instance.functions.balanceOf(to_delegate).call()
                    new_delegatee_member = Member(address=to_delegate, personalBalance=str(balance), delegate="", votingWeight="0")
                    batch.set(to_delegate_member_ref, new_delegatee_member.toJson())
                except Exception as e:
                    print(f"Error creating new delegatee member {to_delegate}: {e}")
                    new_delegatee_member = Member(address=to_delegate, personalBalance="0", delegate="", votingWeight="0")
                    batch.set(to_delegate_member_ref, new_delegatee_member.toJson())

            batch.update(to_delegate_member_ref, {
                "constituents": firestore.ArrayUnion([delegator])
            })

        if from_delegate != self.ZERO_ADDRESS and from_delegate != delegator and from_delegate != to_delegate:
            from_delegate_member_ref = self.daos_collection.document(self.dao).collection('members').document(from_delegate)
            batch.update(from_delegate_member_ref, {
                "constituents": firestore.ArrayRemove([delegator])
            })
        
        try:
            batch.commit()
        except Exception as e:
            print(f"Error committing batch for delegation in DAO {self.dao}: {e}")
        return None

    def propose(self, log):
        if not self.dao:
            print(f"DAO address not set for contract {self.address}, cannot process propose event.")
            return None

        # Handle both raw logs (from real-time) and processed events (from get_logs)
        if 'args' in log:
            # Already processed event from get_logs()
            event = log
        else:
            # Raw log from real-time listener, needs processing
            contract_instance = self.get_contract()
            if not contract_instance:
                return None
            try:
                event = contract_instance.events.ProposalCreated().process_log(log)
            except Exception as e:
                print(f"Error processing ProposalCreated for {self.address} in DAO {self.dao}: {e}")
                return None

        proposal_id_raw = event["args"]["proposalId"]
        proposal_id = str(proposal_id_raw)

        print(f"Processing new proposal {proposal_id} for DAO {self.dao}")

        proposer = Web3.to_checksum_address(event["args"]["proposer"])
        targets = [Web3.to_checksum_address(t) for t in event["args"]["targets"]]
        values = [str(v) for v in event["args"]["values"]]
        calldatas_raw = event["args"]["calldatas"]
        calldatas = [cd.hex() if isinstance(cd, bytes) else str(cd) for cd in calldatas_raw]

        vote_start_block = event["args"]["voteStart"]
        vote_end_block = event["args"]["voteEnd"]
        description_full = event["args"]["description"]
        
        parts = description_full.split("0|||0")
        if len(parts) >= 4:
            name = parts[0] if parts[0] else "(No Title Provided)"
            type_ = parts[1] if parts[1] else "unknown"
            desc = parts[2] if parts[2] else description_full
            link = parts[3] if parts[3] else "(No Link Provided)"
        elif len(parts) == 1 and description_full:
            name = description_full[:80]
            type_ = "custom"
            desc = description_full
            link = "(No Link Provided)"
        else:
            name = "(No Title Provided)"
            type_ = "unknown"
            desc = description_full if description_full else "(No Description Provided)"
            link = "(No Link Provided)"

        p = Proposal(name=name, org=self.dao)
        p.author = proposer
        p.id = proposal_id
        p.type = type_
        p.targets = targets
        p.values = values
        p.description = desc
        p.callDatas = calldatas
        p.createdAt = datetime.now(tz=timezone.utc)
        p.votingStartsBlock = str(vote_start_block)
        p.votingEndsBlock = str(vote_end_block)
        p.externalResource = link
        
        p.totalSupply = "0"
        if self.token_paper and self.token_paper.address:
            token_contract_for_dao = self.get_specific_contract(self.token_paper.address, tokenAbiGlobal)
            if token_contract_for_dao:
                try:
                    p.totalSupply = str(token_contract_for_dao.functions.getPastTotalSupply(vote_start_block).call())
                except Exception as e:
                    print(f"Warning: Could not fetch past total supply for proposal {proposal_id} from token {self.token_paper.address} at block {vote_start_block}. Error: {e}. Attempting to use current total supply.")
                    try:
                        p.totalSupply = str(token_contract_for_dao.functions.totalSupply().call())
                    except Exception as e2:
                        print(f"Error fetching current total supply for proposal {proposal_id}. Error: {e2}. Defaulting to '0'.")
        else:
            print(f"Warning: Token paper or token address not set for DAO {self.dao}. Proposal {proposal_id} will have totalSupply of '0'.")

        proposal_doc_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id)
        try:
            proposal_doc_ref.set(p.toJson())

            member_doc_ref = self.daos_collection.document(self.dao).collection('members').document(proposer)
            if member_doc_ref.get().exists:
                 member_doc_ref.update({"proposalsCreated": firestore.ArrayUnion([proposal_id])})
            else:
                print(f"Proposer {proposer} not found. Creating member entry.")
                balance = "0"
                if self.token_paper and self.token_paper.address:
                    token_contract_for_dao = self.get_specific_contract(self.token_paper.address, tokenAbiGlobal)
                    if token_contract_for_dao:
                        try:
                            balance = str(token_contract_for_dao.functions.balanceOf(proposer).call())
                        except:
                            pass
                new_member = Member(address=proposer, personalBalance=balance, delegate="", votingWeight="0")
                new_member.proposalsCreated = [proposal_id]
                member_doc_ref.set(new_member.toJson())
            
            print(f"Successfully saved proposal {proposal_id} to DAO {self.dao}")

        except Exception as e:
            print(f"Error saving proposal {proposal_id} or updating member {proposer} in DAO {self.dao}: {e}")


    def vote(self, log):
        if not self.dao:
            print(f"DAO address not set for contract {self.address}, cannot process vote event.")
            return None
        # Handle both raw logs (from real-time) and processed events (from get_logs)
        if 'args' in log:
            # Already processed event from get_logs()
            event = log
        else:
            # Raw log from real-time listener, needs processing
            contract_instance = self.get_contract()
            if not contract_instance: return None
            try:
                event = contract_instance.events.VoteCast().process_log(log)
            except Exception as e:
                print(f"Error processing VoteCast for {self.address} in DAO {self.dao}: {e}")
                return None

        proposal_id = str(event["args"]["proposalId"])
        tx_hash_bytes = event['transactionHash']
        tx_hash_hex = tx_hash_bytes.hex()


        voter = Web3.to_checksum_address(event["args"]["voter"])
        support = event["args"]["support"]
        weight = event["args"]["weight"]
        reason = event["args"]["reason"]
        
        vote_obj = Vote(proposalID=proposal_id, votingPower=str(weight), option=support, voter=voter)
        vote_obj.reason = reason
        vote_obj.hash = tx_hash_hex
        
        proposal_votes_collection_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id).collection("votes")
        vote_doc_ref = proposal_votes_collection_ref.document(voter)

        batch = self.db.batch()
        batch.set(vote_doc_ref, vote_obj.toJson())

        member_doc_ref = self.daos_collection.document(self.dao).collection('members').document(voter)
        if member_doc_ref.get().exists:
            batch.update(member_doc_ref, {"proposalsVoted": firestore.ArrayUnion([proposal_id])})
        else:
            print(f"Voter {voter} not found. Creating member entry for vote.")
            balance = "0"
            if self.token_paper and self.token_paper.address:
                token_contract_for_dao = self.get_specific_contract(self.token_paper.address, tokenAbiGlobal)
                if token_contract_for_dao:
                    try:
                        balance = str(token_contract_for_dao.functions.balanceOf(voter).call())
                    except: pass
            new_member = Member(address=voter, personalBalance=balance, delegate="", votingWeight="0")
            new_member.proposalsVoted = [proposal_id]
            batch.set(member_doc_ref, new_member.toJson())
            
        proposal_doc_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id)
        
        @firestore.transactional
        def update_proposal_votes(transaction, proposal_ref, weight_val, support_val):
            proposal_snapshot = proposal_ref.get(transaction=transaction)
            if not proposal_snapshot.exists:
                print(f"Proposal {proposal_id} not found during vote update transaction.")
                return

            prop_data = proposal_snapshot.to_dict()
            
            current_in_favor = int(prop_data.get('inFavor', "0"))
            current_against = int(prop_data.get('against', "0"))
            
            current_votes_for = prop_data.get('votesFor', 0)
            current_votes_against = prop_data.get('votesAgainst', 0)

            if support_val == 1:
                new_in_favor = current_in_favor + weight_val
                transaction.update(proposal_ref, {
                    'inFavor': str(new_in_favor),
                    'votesFor': current_votes_for + 1
                })
            elif support_val == 0:
                new_against = current_against + weight_val
                transaction.update(proposal_ref, {
                    'against': str(new_against),
                    'votesAgainst': current_votes_against + 1
                })

        try:
            transaction = self.db.transaction()
            update_proposal_votes(transaction, proposal_doc_ref, int(weight), support)
            transaction.commit()
            batch.commit()
            print(f"Vote by {voter} on proposal {proposal_id} processed.")
        except Exception as e:
            print(f"Error during vote processing for proposal {proposal_id} by {voter}: {e}")


    def queue(self, log):
        if not self.dao:
            print(f"DAO address not set for contract {self.address}, cannot process queue event.")
            return None
        # Handle both raw logs (from real-time) and processed events (from get_logs)
        if 'args' in log:
            # Already processed event from get_logs()
            event = log
        else:
            # Raw log from real-time listener, needs processing
            contract_instance = self.get_contract()
            if not contract_instance: return None
            try:
                event = contract_instance.events.ProposalQueued().process_log(log)
            except Exception as e:
                print(f"Error processing ProposalQueued for {self.address} in DAO {self.dao}: {e}")
                return None

        proposal_id = str(event['args']['proposalId'])
        
        proposal_doc_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id)
        
        try:
            proposal_doc_ref.update({
                "statusHistory.queued": datetime.now(tz=timezone.utc),
                "latestStage": "Queued",
                
            })
            print(f"Proposal {proposal_id} queued in DAO {self.dao}.")
        except Exception as e:
            print(f"Error updating proposal {proposal_id} on queue event in DAO {self.dao}: {e}")


    def bytes_to_int(self, byte_array):
        return int.from_bytes(byte_array, byteorder='big')

    def decode_params(self, data_bytes_hex):
        if not isinstance(data_bytes_hex, str):
            print(f"decode_params expects a hex string, got {data_bytes_hex}")
            return None, None
        
        if data_bytes_hex.startswith("0x"):
            data_bytes_hex = data_bytes_hex[2:]

        try:
            data_bytes = bytes.fromhex(data_bytes_hex)
        except ValueError as e:
            print(f"Error converting hex to bytes in decode_params: {data_bytes_hex}, error: {e}")
            return None, None

        data_without_selector = data_bytes[4:]
        if len(data_without_selector) < 64:
            print("Not enough data for two offsets in decode_params")
            return None,None

        param1_offset_bytes = data_without_selector[:32]
        param2_offset_bytes = data_without_selector[32:64]
        param1_offset = self.bytes_to_int(param1_offset_bytes)
        param2_offset = self.bytes_to_int(param2_offset_bytes)
        
        if param1_offset + 32 > len(data_without_selector):
             print("Param1 offset out of bounds")
             return None,None
        param1_length_bytes = data_without_selector[param1_offset : param1_offset + 32]
        param1_length = self.bytes_to_int(param1_length_bytes)
        if param1_offset + 32 + param1_length > len(data_without_selector):
            print("Param1 length out of bounds")
            return None, None
        param1_data_bytes = data_without_selector[param1_offset + 32 : param1_offset + 32 + param1_length]
        param1_data = param1_data_bytes.decode('utf-8', errors='replace')

        if param2_offset + 32 > len(data_without_selector):
            print("Param2 offset out of bounds")
            return None,None
        param2_length_bytes = data_without_selector[param2_offset : param2_offset + 32]
        param2_length = self.bytes_to_int(param2_length_bytes)
        if param2_offset + 32 + param2_length > len(data_without_selector):
            print("Param2 length out of bounds")
            return None, None
        param2_data_bytes = data_without_selector[param2_offset + 32 : param2_offset + 32 + param2_length]
        param2_data = param2_data_bytes.decode('utf-8', errors='replace')
        
        return param1_data, param2_data

     
    def execute(self, log):
        if not self.dao:
            print(f"DAO address not set for contract {self.address}, cannot process execute event.")
            return None
        # Handle both raw logs (from real-time) and processed events (from get_logs)
        if 'args' in log:
            # Already processed event from get_logs()
            event = log
        else:
            # Raw log from real-time listener, needs processing
            contract_instance = self.get_contract()
            if not contract_instance: return None
            try:
                event = contract_instance.events.ProposalExecuted().process_log(log)
            except Exception as e:
                print(f"Error processing ProposalExecuted for {self.address} in DAO {self.dao}: {e}")
                return None

        proposal_id = str(event['args']['proposalId'])
        print(f"Executing proposal id: {proposal_id} in DAO {self.dao}")
        
        proposal_doc_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id)
        proposal_snapshot = proposal_doc_ref.get()

        if not proposal_snapshot.exists:
            print(f"Proposal {proposal_id} not found in DB for DAO {self.dao} during execution.")
            return

        prop_data_from_db = proposal_snapshot.to_dict()
        
        updates_for_proposal = {
            "statusHistory.executed": datetime.now(tz=timezone.utc),
            "latestStage": "Executed",
            "executionHash": event['transactionHash'].hex()
        }

        proposal_type = prop_data_from_db.get('type', "").lower()
        proposal_calldatas = prop_data_from_db.get('callDatas', [])
        proposal_targets_db = prop_data_from_db.get('targets', [])

        dao_doc_ref = self.daos_collection.document(self.dao)
        dao_updates = {}

        try:
            if "voting period" in proposal_type and proposal_calldatas:
                calldata_bytes = bytes.fromhex(proposal_calldatas[0])
                decoded = decode_function_parameters(voting_period_function_abi, calldata_bytes)
                if decoded and len(decoded) > 0:
                    new_voting_period_seconds = int(decoded[0])
                    # Convert seconds to minutes
                    dao_updates["votingDuration"] = new_voting_period_seconds // 60
                    print(f"DAO {self.dao} voting period updated to {dao_updates['votingDuration']} minutes")

            if "threshold" in proposal_type and proposal_calldatas:
                calldata_bytes = bytes.fromhex(proposal_calldatas[0])
                decoded = decode_function_parameters(proposal_threshold_function_abi, calldata_bytes)
                if decoded and len(decoded) > 0:
                    new_raw_threshold = int(decoded[0])
                    dao_snapshot = dao_doc_ref.get()
                    if dao_snapshot.exists:
                        current_dao_data = dao_snapshot.to_dict()
                        decimals = int(current_dao_data.get('decimals', 18))
                        new_proposal_threshold_adjusted = str(new_raw_threshold // (10**decimals))
                        dao_updates["proposalThreshold"] = str(new_proposal_threshold_adjusted)
                        print(f"DAO {self.dao} proposal threshold updated to {new_proposal_threshold_adjusted} (adjusted from raw {new_raw_threshold})")
                    else:
                        print(f"Could not fetch DAO data to adjust proposal threshold for DAO {self.dao}")
                        dao_updates["proposalThreshold"] = str(new_raw_threshold)

            if "delay" in proposal_type and proposal_calldatas:
                calldata_bytes = bytes.fromhex(proposal_calldatas[0])
                decoded = decode_function_parameters(voting_delay_function_abi, calldata_bytes)
                if decoded and len(decoded) > 0:
                    new_voting_delay_seconds = int(decoded[0])
                    # Convert seconds to minutes
                    dao_updates["votingDelay"] = new_voting_delay_seconds // 60
                    print(f"DAO {self.dao} voting delay updated to {dao_updates['votingDelay']} minutes")
            
            if "timelock delay" in proposal_type and proposal_calldatas:
                pass


            if "quorum" in proposal_type and proposal_calldatas:
                calldata_bytes = bytes.fromhex(proposal_calldatas[0])
                decoded = decode_function_parameters(quorum_function_abi, calldata_bytes)
                if decoded and len(decoded) > 0:
                    dao_updates["quorum"] = int(decoded[0])
                    print(f"DAO {self.dao} quorum updated to {int(decoded[0])}")

            if proposal_type == "registry" and proposal_calldatas:
                key, value = self.decode_params(proposal_calldatas[0])
                if key is not None and value is not None:
                    current_dao_data = dao_doc_ref.get().to_dict()
                    registry_map = current_dao_data.get("registry", {})
                    registry_map[key] = value
                    dao_updates["registry"] = registry_map
                    print(f"DAO {self.dao} registry updated: {key} -> {value}")
                    # Also update the description field if key is "description"
                    if key == "description":
                        dao_updates["description"] = value
                        print(f"DAO {self.dao} description updated: {value[:50]}...")


            if "mint" in proposal_type.lower() or "burn" in proposal_type.lower() and proposal_calldatas and proposal_targets_db:
                print(f"Processing mint/burn for proposal {proposal_id} in DAO {self.dao}")
                token_address_target = Web3.to_checksum_address(proposal_targets_db[0])
                target_token_contract = self.get_specific_contract(token_address_target, tokenAbiGlobal)

                if target_token_contract:
                    try:
                        calldata_bytes = bytes.fromhex(proposal_calldatas[0])
                        params = decode_function_parameters(function_abi=mint_function_abi, data_bytes=calldata_bytes)
                        if params and len(params) > 0:
                            member_address_affected = Web3.to_checksum_address(params[0])
                            
                            new_balance = target_token_contract.functions.balanceOf(member_address_affected).call()
                            member_doc_ref = self.daos_collection.document(self.dao).collection('members').document(member_address_affected)
                            if member_doc_ref.get().exists:
                                member_doc_ref.update({"personalBalance": str(new_balance)})
                            else:
                                print(f"Member {member_address_affected} not found for mint/burn. Creating.")
                                new_member = Member(address=member_address_affected, personalBalance=str(new_balance), delegate="", votingWeight="0")
                                member_doc_ref.set(new_member.toJson())
                            print(f"Member {member_address_affected} balance updated to {new_balance} after mint/burn.")

                            new_total_supply = target_token_contract.functions.totalSupply().call()
                            dao_updates["totalSupply"] = str(new_total_supply)
                            print(f"DAO {self.dao} total supply updated to {new_total_supply} after mint/burn.")
                    except Exception as e:
                        print(f"Error decoding/processing mint/burn params for proposal {proposal_id}: {e}")
                else:
                    print(f"Could not get contract for target token {token_address_target} in mint/burn.")

            

            if dao_updates:
                dao_doc_ref.update(dao_updates)
            proposal_doc_ref.update(updates_for_proposal)
            print(f"Proposal {proposal_id} execution processed for DAO {self.dao}.")

        except Exception as e:
            import traceback
            print(f"Error during proposal execution processing for prop {proposal_id}, DAO {self.dao}: {e}")
            print(traceback.format_exc())



    def registry_updated(self, log):
        """
        Handle RegistryUpdated event from Registry contracts.
        Updates the DAO's description field when key is 'description'.
        
        Event: RegistryUpdated(string key, string value)
        """
        if not self.dao:
            print(f"Registry paper at {self.address} has no DAO address set, cannot process RegistryUpdated")
            return None
            
        contract_instance = self.get_contract()
        if not contract_instance:
            print(f"Could not get contract instance for registry {self.address}")
            return None

        try:
            decoded_event = contract_instance.events.RegistryUpdated().process_log(log)
        except Exception as e:
            print(f"Error processing RegistryUpdated log for {self.address}: {e}")
            return None

        args = decoded_event['args']
        key = args['key']
        value = args['value']
        
        print(f"RegistryUpdated: key='{key}' for DAO {self.dao}")

        # If key is 'description', update the DAO's description field
        if key == 'description':
            try:
                dao_doc_ref = self.daos_collection.document(self.dao)
                dao_doc_ref.update({
                    'description': value,
                    'registry.description': value  # Also update in registry map
                })
                print(f"Updated description for DAO {self.dao}: {value[:50]}...")
            except Exception as e:
                print(f"Error updating description for DAO {self.dao}: {e}")
        else:
            # For other keys, just update the registry map
            try:
                dao_doc_ref = self.daos_collection.document(self.dao)
                dao_doc_ref.update({
                    f'registry.{key}': value
                })
                print(f"Updated registry key '{key}' for DAO {self.dao}")
            except Exception as e:
                print(f"Error updating registry key '{key}' for DAO {self.dao}: {e}")
        
        return None


    def handle_event(self, log, func=None):
        # Debug logging
        print(f"[DEBUG handle_event] kind={self.kind}, func={func}, address={self.address}")

        if self.kind == "wrapper":
            if func == "NewDaoCreated":
                return self.add_dao(log)
        elif self.kind == "wrapper_t":
            if func == "NewDaoCreated":
                # Transferable non-wrapped tokens
                return self.add_dao(log)
        elif self.kind == "wrapper_trustless":
            if func == "NewDaoCreated":
                return self.add_dao(log)
            elif func == "SuiteConfigured":
                return self.configure_economy(log)
        elif self.kind == "wrapper_w":
            print(f"[DEBUG wrapper_w] Event detected: {func}")
            if func == "NewDaoCreated":
                # For backward compatibility with old wrapper_w that used NewDaoCreated
                return self.add_dao(log)
            elif func == "DaoWrappedDeploymentInfo":
                print(f"[DEBUG wrapper_w] Calling add_dao_wrapped()")
                # New StandardFactoryWrapped uses DaoWrappedDeploymentInfo for wrapped ERC20 tokens
                return self.add_dao_wrapped(log)
        elif self.kind == "token":
            if func == "DelegateChanged":
                self.delegate(log)
        elif self.kind == "dao":
            if func == "ProposalCreated":
                self.propose(log)
            elif func == "VoteCast":
                self.vote(log)
            elif func == "ProposalQueued":
                self.queue(log)
            elif func == "ProposalExecuted":
                self.execute(log)
        elif self.kind == "registry":
            if func == "RegistryUpdated":
                self.registry_updated(log)
        return None
# apps/homebase/paper.py
# apps/homebase/paper.py

from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal, wrapper_token_abi,  timelock_min_delay_abi
from datetime import datetime, timezone, timedelta
from apps.homebase.entities import ProposalStatus, Proposal, StateInContract, Txaction, Token, Member, Org, Vote
import re
from web3 import Web3
from google.cloud import firestore
import codecs
from apps.generic.converting import decode_function_parameters
from apps.homebase.eventSignatures import quorum_function_abi, voting_period_function_abi,proposal_threshold_function_abi, voting_delay_function_abi


class Paper:
    ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
    def __init__(self, address, kind, web3, daos_collection, db, dao=None, token=None):
        self.address = address
        self.kind = kind
        self.contract = None
        self.dao = dao
        self.token_paper: Paper = token
        self.web3: Web3 = web3
        self.daos_collection = daos_collection
        self.db = db
        self.abi_string = None

        if kind == "wrapper":
            self.abi_string = wrapperAbi
        elif kind == "wrapper_w":
            try:
                from apps.homebase.abis import wrapper_w_abi
                self.abi_string = wrapper_w_abi
            except ImportError:
                print(f"Warning: wrapper_w_abi not found for Paper kind {kind}. Falling back to generic event processing or ensure event name is unique.")
                self.abi_string = wrapperAbi
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

    # --- FIX #1: Made this helper function robust to handle both string and dict/list ABIs ---
    # This prevents the app from crashing when an ABI is passed as a Python dictionary.
    def get_specific_contract(self, address, abi):
        """Helper to get a contract instance with a specific address and ABI."""
        try:
            final_abi = abi
            if isinstance(abi, str):  # Only clean the ABI if it's a string
                final_abi = re.sub(r'\n+', ' ', abi).strip()
            
            # Web3.py can handle string, dictionary, or list of dictionary ABIs
            return self.web3.eth.contract(
                address=Web3.to_checksum_address(address), abi=final_abi
            )
        except Exception as e:
            print(f"Error creating specific contract {address}: {e}")
            return None
    # --- END OF FIX #1 ---

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
        print(f"New DAO (original wrapper): {name} from event")
        
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
        else:
            org.decimals = 18

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
        
        if len(amounts) >= len(members) + 4:
            settings_start_index = len(amounts) - 4
            org.votingDelay = amounts[settings_start_index]
            org.votingDuration = amounts[settings_start_index + 1]
            org.proposalThreshold = str(amounts[settings_start_index + 2])
            org.quorum = amounts[settings_start_index + 3]
        else:
            print(f"Warning: Could not extract DAO settings from initialAmounts for {name}")
            org.votingDelay = 0 
            org.votingDuration = 0
            org.proposalThreshold = "0"
            org.quorum = 0

        org.executionDelay = args['executionDelay']

        self.daos_collection.document(org.address).set(org.toJson())
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
        
        org.holders = 0 

        # --- FIX #2: Use the correct ABI for the wrapped token contract ---
        # This allows the .underlying() function call to succeed.
        wrapped_token_contract = self.get_specific_contract(org.govTokenAddress, wrapper_token_abi)
        # --- END OF FIX #2 ---

        if wrapped_token_contract:
            try:
                org.decimals = wrapped_token_contract.functions.decimals().call()
                org.totalSupply = str(wrapped_token_contract.functions.totalSupply().call())
                # This call will now succeed because we are using the correct ABI
                org.underlyingToken = str(wrapped_token_contract.functions.underlying().call()) 
            except Exception as e:
                print(f"Error fetching info for wrapped token {org.govTokenAddress}: {e}")
                org.decimals = 18
                org.totalSupply = "0"
                # org.underlyingToken will remain None, which is handled in toJson()
        else:
            org.decimals = 18
            org.totalSupply = "0"

        dao_contract = self.get_specific_contract(org.address, daoAbiGlobal)
        if dao_contract:
            try:
                from apps.homebase.abis import governor_proposal_threshold_abi, governor_voting_delay_abi, governor_voting_period_abi, governor_timelock_abi
                
                raw_threshold = dao_contract.functions.proposalThreshold().call()
                org.proposalThreshold = str(raw_threshold)
                
                org.votingDelay = dao_contract.functions.votingDelay().call()
                org.votingDuration = dao_contract.functions.votingPeriod().call()

                timelock_address = dao_contract.functions.timelock().call()
                # This call now works because get_specific_contract was fixed to handle dict ABIs
                timelock_contract = self.get_specific_contract(timelock_address, [timelock_min_delay_abi])
                if timelock_contract:
                    org.executionDelay = timelock_contract.functions.getMinDelay().call()
                else:
                    org.executionDelay = 0
            except Exception as e:
                print(f"Error fetching DAO/Timelock settings for {org.address}: {e}")
                org.proposalThreshold = "0"
                org.votingDelay = 0
                org.votingDuration = 0
                org.executionDelay = 0
        else:
            org.proposalThreshold = "0"
            org.votingDelay = 0
            org.votingDuration = 0
            org.executionDelay = 0
            
        org.registry = {}

        self.daos_collection.document(org.address).set(org.toJson())
        print(f"Successfully added DAO (wrapped) {org.name} / {org.address} to Firestore.")
        return [org.address, org.govTokenAddress]


    def delegate(self, log):
        if not self.dao:
            print(f"DAO address not set for token {self.address}, cannot process delegate event.")
            return None
            
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
        if not isinstance(data_bytes_hex, str) or not data_bytes_hex.startswith("0x"):
            print(f"decode_params expects a hex string starting with 0x, got {data_bytes_hex}")
            return None, None
        try:
            data_bytes = bytes.fromhex(data_bytes_hex[2:])
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
                decoded = decode_function_parameters(voting_period_function_abi, proposal_calldatas[0])
                if decoded and len(decoded) > 0:
                    new_voting_period_seconds = int(decoded[0])
                    dao_updates["votingDuration"] = new_voting_period_seconds 
                    print(f"DAO {self.dao} voting period updated to {new_voting_period_seconds}")

            if "threshold" in proposal_type and proposal_calldatas:
                decoded = decode_function_parameters(proposal_threshold_function_abi, proposal_calldatas[0])
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
                decoded = decode_function_parameters(voting_delay_function_abi, proposal_calldatas[0])
                if decoded and len(decoded) > 0:
                    new_voting_delay_value = int(decoded[0])
                    dao_updates["votingDelay"] = new_voting_delay_value
                    print(f"DAO {self.dao} voting delay updated to {new_voting_delay_value}")
            
            if "timelock delay" in proposal_type and proposal_calldatas:
                pass


            if "quorum" in proposal_type and proposal_calldatas:
                decoded = decode_function_parameters(quorum_function_abi, proposal_calldatas[0])
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


            if "mint" in proposal_type.lower() or "burn" in proposal_type.lower() and proposal_calldatas and proposal_targets_db:
                print(f"Processing mint/burn for proposal {proposal_id} in DAO {self.dao}")
                token_address_target = Web3.to_checksum_address(proposal_targets_db[0])
                target_token_contract = self.get_specific_contract(token_address_target, tokenAbiGlobal)

                if target_token_contract:
                    try:
                        params = decode_function_parameters(function_abi=mint_function_abi, data_bytes=proposal_calldatas[0])
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


    def handle_event(self, log, func=None):
        if self.kind == "wrapper":
            if func == "NewDaoCreated":
                return self.add_dao(log)
        elif self.kind == "wrapper_w":
            if func == "DaoWrappedDeploymentInfo":
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
        return None
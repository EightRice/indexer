from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal, mint_function_abi, burn_function_abi # Add wrapper_w_abi if different
from datetime import datetime, timezone, timedelta # timedelta might be useful
from apps.homebase.entities import ProposalStatus, Proposal, StateInContract, Txaction, Token, Member, Org, Vote
import re
from web3 import Web3
from google.cloud import firestore
import codecs # Not used in current snippet, can remove if not needed elsewhere
from apps.generic.converting import decode_function_parameters # Ensure this path is correct
from apps.homebase.eventSignatures import quorum_function_abi, voting_period_function_abi,proposal_threshold_function_abi, voting_delay_function_abi, timelock_min_delay_abi, governor_proposal_threshold_abi, governor_voting_delay_abi, governor_voting_period_abi # Added more ABIs for fetching

# It's good practice to define ABIs for functions you'll call, e.g., for fetching DAO settings
# Ensure these are defined in eventSignatures.py or here
# Example:
# timelock_min_delay_abi = [{"constant":True,"inputs":[],"name":"getMinDelay","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"}]
# governor_proposal_threshold_abi = [{"constant":True,"inputs":[],"name":"proposalThreshold","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"}]
# governor_voting_delay_abi = [{"constant":True,"inputs":[],"name":"votingDelay","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"}]
# governor_voting_period_abi = [{"constant":True,"inputs":[],"name":"votingPeriod","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"}]
# governor_timelock_abi = [{"constant":True,"inputs":[],"name":"timelock","outputs":[{"internalType":"address","name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}]
# erc20_wrapper_underlying_abi = [{"constant":True,"inputs":[],"name":"underlying","outputs":[{"internalType":"address","name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}]


class Paper:
    ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
    def __init__(self, address, kind, web3, daos_collection, db, dao=None, token=None):
        self.address = address
        self.kind = kind
        self.contract = None
        self.dao = dao # This is the DAO address string
        self.token_paper: Paper = token # Renamed to token_paper to avoid conflict with a Token entity
        self.web3: Web3 = web3
        self.daos_collection = daos_collection
        self.db = db
        self.abi_string = None # To store the raw ABI string

        # You might need different ABIs for WrapperContract and WrapperContract_W
        # For now, assuming wrapperAbi can decode events from both if they share names,
        # but specific contract interaction might need specific ABIs.
        if kind == "wrapper": # Original Wrapper
            self.abi_string = wrapperAbi
        elif kind == "wrapper_w": # New Wrapper for Wrapped Tokens
            # If WrapperContract_W has a different ABI for its functions (not events), load it here
            # For event decoding, the event signature in app.py is key.
            # For now, assume its event can be decoded by a generic approach or by having its ABI.
            # Let's assume you have a wrapper_w_abi defined similarly to wrapperAbi
            # from apps.homebase.abis import wrapper_w_abi # You would need to define this
            # self.abi_string = wrapper_w_abi 
            # For simplicity, if only used for event decoding via process_log, might not need full ABI here
            # if the event name is unique and handled in handle_event.
            # The get_contract().events.YourEventName().process_log(log) needs the contract's ABI.
            # For now, we'll assume WrapperContract_W ABI is needed for its specific event.
            # Placeholder: use wrapperAbi if wrapper_w_abi is not defined yet,
            # but ideally, it should have its own.
            try:
                from apps.homebase.abis import wrapper_w_abi
                self.abi_string = wrapper_w_abi
            except ImportError:
                print(f"Warning: wrapper_w_abi not found for Paper kind {kind}. Falling back to generic event processing or ensure event name is unique.")
                self.abi_string = wrapperAbi # Fallback, might not be ideal
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

    def get_specific_contract(self, address, abi_str):
        """Helper to get a contract instance with a specific address and ABI string."""
        try:
            clean_abi = re.sub(r'\n+', ' ', abi_str).strip()
            return self.web3.eth.contract(address=Web3.to_checksum_address(address), abi=clean_abi)
        except Exception as e:
            print(f"Error creating specific contract {address}: {e}")
            return None

    def add_dao(self, log): # Handles NewDaoCreated from original WrapperContract
        contract_instance = self.get_contract()
        if not contract_instance:
            print(f"Could not get contract instance for {self.address} in add_dao")
            return None
        try:
            decoded_event = contract_instance.events.NewDaoCreated().process_log(log)
        except Exception as e:
            print(f"Error processing NewDaoCreated log with ABI for {self.address}: {e}")
            # Potentially try a more generic decoding if ABI is mismatched, or re-throw
            return None

        args = decoded_event['args']
        name = args['name']
        print(f"New DAO (original wrapper): {name} from event")
        
        org = Org(name=name)
        org.creationDate = datetime.now(timezone.utc) # Use timezone.utc
        org.govTokenAddress = args['token']
        org.address = args['dao']
        org.symbol = args['symbol']
        org.registryAddress = args['registry']
        org.description = args['description']
        members = args['initialMembers']
        amounts = args['initialAmounts'] # This is the combined array
        org.holders = len(members) if members else 0

        token_contract = self.get_specific_contract(org.govTokenAddress, tokenAbiGlobal)
        if token_contract:
            try:
                org.decimals = token_contract.functions.decimals().call()
            except Exception as e:
                print(f"Error fetching decimals for token {org.govTokenAddress}: {e}")
                org.decimals = 18 # Default or handle error
        else:
            org.decimals = 18 # Default

        supply = 0
        batch = self.db.batch()
        # Amounts for members are the first len(members) elements of args['initialAmounts']
        for i in range(len(members)):
            member_address_checksum = Web3.to_checksum_address(members[i])
            member_balance = amounts[i] # This is correct as per original logic
            supply += member_balance
            m = Member(address=member_address_checksum, personalBalance=str(member_balance), delegate="", votingWeight="0")
            member_doc_ref = self.daos_collection.document(org.address).collection('members').document(m.address)
            batch.set(member_doc_ref, m.toJson())
        
        org.totalSupply = str(supply) # Sum of initial member allocations

        keys = args['keys']
        values = args['values']
        if keys and values and len(keys) == len(values): # More robust check
            org.registry = {keys[i]: values[i] for i in range(len(keys)) if keys[i] and values[i]}
        else:
            org.registry = {}
        
        # DAO settings are at the end of 'initialAmounts'
        if len(amounts) >= len(members) + 4:
            settings_start_index = len(amounts) - 4
            org.votingDelay = amounts[settings_start_index]      # originally minsDelay * 1 minutes
            org.votingDuration = amounts[settings_start_index + 1]  # originally minsVoting * 1 minutes
            org.proposalThreshold = str(amounts[settings_start_index + 2])
            org.quorum = amounts[settings_start_index + 3]          # originally qvrm (percentage)
        else: # Fallback or error if settings not found
            print(f"Warning: Could not extract DAO settings from initialAmounts for {name}")
            # Set defaults or fetch later if necessary
            org.votingDelay = 0 
            org.votingDuration = 0
            org.proposalThreshold = "0"
            org.quorum = 0

        org.executionDelay = args['executionDelay'] # Timelock execution delay

        self.daos_collection.document(org.address).set(org.toJson())
        try:
            batch.commit()
            print(f"Successfully added DAO {org.name} / {org.address} to Firestore.")
        except Exception as e:
            print(f"Error committing batch for DAO {org.name}: {e}")

        return [org.address, org.govTokenAddress]

    def add_dao_wrapped(self, log): # Handles DaoWrappedDeploymentInfo from WrapperContract_W
        # Since the event is specific, we might need a way to get the correct ABI for WrapperContract_W
        # or ensure the event name + topics are enough if not using contract.events.EventName()
        # For now, assuming the event_name in app.py correctly routes here.
        # We'll decode generically or assume self.get_contract() has Wrapper_W ABI if kind='wrapper_w'
        
        contract_instance = self.get_contract() # This should be WrapperContract_W instance
        if not contract_instance:
            print(f"Could not get contract instance for {self.address} in add_dao_wrapped")
            return None
        try:
            # IMPORTANT: The event name here MUST match what's in your WrapperContract_W ABI
            # and what you mapped in app.py's event_signatures
            decoded_event = contract_instance.events.DaoWrappedDeploymentInfo().process_log(log)
        except Exception as e:
            print(f"Error processing DaoWrappedDeploymentInfo log with ABI for {self.address}: {e}")
            # Fallback: try to decode with known types if process_log fails due to ABI mismatch
            # This is more complex and error-prone. Best to have correct ABI for WrapperContract_W.
            # For now, we'll assume ABI is correct or process_log works.
            # If it fails consistently, we need to ensure Wrapper_W's ABI is loaded for kind "wrapper_w".
            # from eth_abi import decode
            # event_abi_entry = next((item for item in self.abi if item.get("type") == "event" and item.get("name") == "DaoWrappedDeploymentInfo"), None)
            # if event_abi_entry:
            #     types = [inp['type'] for inp in event_abi_entry['inputs'] if not inp['indexed']]
            #     # topics = [log['topics'][i+1] for i in range(len(event_abi_entry['inputs'])) if event_abi_entry['inputs'][i]['indexed']]
            #     # decoded_unindexed = decode(types, log['data'])
            #     # This is a simplified example, proper decoding is more involved.
            # else:
            #     print("DaoWrappedDeploymentInfo ABI entry not found for manual decoding.")
            # return None
            return None


        args = decoded_event['args']
        dao_name = args['daoName'] # This is also the wrapped token name
        print(f"New DAO (wrapped wrapper): {dao_name} from event")

        org = Org(name=dao_name)
        org.creationDate = datetime.now(timezone.utc)
        org.govTokenAddress = args['wrappedTokenAddress'] # This is the HBEVM_Wrapped_Token
        org.address = args['daoAddress']
        org.symbol = args['wrappedTokenSymbol']
        org.registryAddress = args['registryAddress']
        org.description = args['description']
        org.quorum = args['quorumFraction'] # Directly from event

        # For wrapped tokens, initialMembers and initialAmounts are not applicable from event
        org.holders = 0 # Or 1 if DAO deployer is considered a member initially
        
        # Fetch decimals from the wrapped token
        wrapped_token_contract = self.get_specific_contract(org.govTokenAddress, tokenAbiGlobal) # Assuming wrapped token has ERC20 interface
        if wrapped_token_contract:
            try:
                org.decimals = wrapped_token_contract.functions.decimals().call()
                # Total supply of wrapped token starts at 0, users need to wrap
                org.totalSupply = str(wrapped_token_contract.functions.totalSupply().call())
            except Exception as e:
                print(f"Error fetching info for wrapped token {org.govTokenAddress}: {e}")
                org.decimals = 18 # Default
                org.totalSupply = "0"
        else:
            org.decimals = 18 # Default
            org.totalSupply = "0"

        # Fetch other DAO settings and timelock delay by calling the contracts
        dao_contract = self.get_specific_contract(org.address, daoAbiGlobal)
        if dao_contract:
            try:
                # Fetch proposalThreshold (ensure ABI for this is in daoAbiGlobal)
                # It's often a large number, store as string
                from apps.homebase.eventSignatures import governor_proposal_threshold_abi, governor_voting_delay_abi, governor_voting_period_abi, governor_timelock_abi
                
                raw_threshold = dao_contract.functions.proposalThreshold().call()
                org.proposalThreshold = str(raw_threshold)
                
                # votingDelay from Governor is in blocks, convert if necessary, or store as blocks
                # Your original code converted minutes to blocks or vice-versa.
                # Here, we're fetching what the contract returns.
                org.votingDelay = dao_contract.functions.votingDelay().call() # in blocks
                org.votingDuration = dao_contract.functions.votingPeriod().call() # in blocks

                timelock_address = dao_contract.functions.timelock().call()
                timelock_contract = self.get_specific_contract(timelock_address, timelock_min_delay_abi) # Need Timelock ABI
                if timelock_contract:
                    org.executionDelay = timelock_contract.functions.getMinDelay().call() # in seconds
                else:
                    org.executionDelay = 0 # Default
            except Exception as e:
                print(f"Error fetching DAO/Timelock settings for {org.address}: {e}")
                org.proposalThreshold = "0"
                org.votingDelay = 0
                org.votingDuration = 0
                org.executionDelay = 0
        else: # Fallback if DAO contract instance fails
            org.proposalThreshold = "0"
            org.votingDelay = 0
            org.votingDuration = 0
            org.executionDelay = 0
            
        # Registry keys/values are not in the event, need to be fetched or set by user later via proposals
        # For now, initialize as empty. If params from WrapperContract_W had keys/values, they are set in _finalizeDeployment_W
        # So, we can try to fetch them from the registry contract if needed immediately.
        # For now, keeping it simple:
        org.registry = {} # Or fetch from org.registryAddress if critical at this point

        self.daos_collection.document(org.address).set(org.toJson())
        print(f"Successfully added DAO (wrapped) {org.name} / {org.address} to Firestore.")
        return [org.address, org.govTokenAddress]


    def delegate(self, log):
        # ... (delegate logic remains largely the same) ...
        # Ensure self.dao (DAO address) is correctly set for this Paper instance
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
        
        # Ensure delegator exists as a member, create if not (e.g., if they wrapped tokens but weren't an initial member)
        delegator_doc = delegator_member_ref.get()
        if not delegator_doc.exists:
            print(f"Delegator {delegator} not found as member in DAO {self.dao}. Creating.")
            # Attempt to get their balance from the token contract (self.address is the token)
            try:
                token_contract_instance = self.get_contract() # self is the token Paper object
                balance = token_contract_instance.functions.balanceOf(delegator).call()
                # Voting weight might be updated by a separate DelegateVotesChanged event or fetched.
                # For now, new member with balance, delegate set, 0 initial voting weight.
                new_member = Member(address=delegator, personalBalance=str(balance), delegate=to_delegate, votingWeight="0")
                batch.set(delegator_member_ref, new_member.toJson())
            except Exception as e:
                print(f"Error creating new member {delegator} for delegation: {e}")
                # Fallback: create with 0 balance if token interaction fails
                new_member = Member(address=delegator, personalBalance="0", delegate=to_delegate, votingWeight="0")
                batch.set(delegator_member_ref, new_member.toJson())

        else: # Delegator exists, update their delegate
            batch.update(delegator_member_ref, {"delegate": to_delegate})

        # Manage constituents
        if to_delegate != self.ZERO_ADDRESS and to_delegate != delegator:
            to_delegate_member_ref = self.daos_collection.document(self.dao).collection('members').document(to_delegate)
            # Ensure to_delegate exists as a member, create if not
            to_delegate_doc = to_delegate_member_ref.get()
            if not to_delegate_doc.exists:
                print(f"Delegatee {to_delegate} not found as member in DAO {self.dao}. Creating.")
                try:
                    token_contract_instance = self.get_contract()
                    balance = token_contract_instance.functions.balanceOf(to_delegate).call()
                    new_delegatee_member = Member(address=to_delegate, personalBalance=str(balance), delegate="", votingWeight="0") # Delegatee might not have a delegate themselves
                    batch.set(to_delegate_member_ref, new_delegatee_member.toJson())
                except Exception as e:
                    print(f"Error creating new delegatee member {to_delegate}: {e}")
                    new_delegatee_member = Member(address=to_delegate, personalBalance="0", delegate="", votingWeight="0")
                    batch.set(to_delegate_member_ref, new_delegatee_member.toJson())

            # Add delegator to to_delegate's constituents list
            batch.update(to_delegate_member_ref, {
                "constituents": firestore.ArrayUnion([delegator])
            })

        if from_delegate != self.ZERO_ADDRESS and from_delegate != delegator and from_delegate != to_delegate:
            from_delegate_member_ref = self.daos_collection.document(self.dao).collection('members').document(from_delegate)
            # Remove delegator from from_delegate's constituents list
            batch.update(from_delegate_member_ref, {
                "constituents": firestore.ArrayRemove([delegator])
            })
        
        try:
            batch.commit()
        except Exception as e:
            print(f"Error committing batch for delegation in DAO {self.dao}: {e}")
        return None


    def propose(self, log):
        # ... (propose logic should be mostly fine, ensure self.dao is correct) ...
        if not self.dao:
            print(f"DAO address not set for contract {self.address}, cannot process propose event.")
            return None
        # Ensure self.token_paper is valid if used
        if not self.token_paper or not self.token_paper.address:
            print(f"Token paper or token address not set for DAO {self.dao}, cannot get token contract for totalSupply.")
            # Decide on fallback or error
            return None

        contract_instance = self.get_contract()
        if not contract_instance: return None
        try:
            event = contract_instance.events.ProposalCreated().process_log(log)
        except Exception as e:
            print(f"Error processing ProposalCreated for {self.address} in DAO {self.dao}: {e}")
            return None
            
        proposal_id_raw = event["args"]["proposalId"]
        proposal_id = str(proposal_id_raw) # Ensure it's a string for Firestore path

        proposer = Web3.to_checksum_address(event["args"]["proposer"])
        # dao_address = Web3.to_checksum_address(event['address']) # event['address'] is the DAO contract emitting this
        
        targets = [Web3.to_checksum_address(t) for t in event["args"]["targets"]]
        values = [str(v) for v in event["args"]["values"]] # Ensure values are strings
        # signatures = event["args"]["signatures"] # Not used in your current Proposal entity
        calldatas_raw = event["args"]["calldatas"]
        calldatas = [cd.hex() if isinstance(cd, bytes) else str(cd) for cd in calldatas_raw]


        vote_start_block = event["args"]["voteStart"]
        vote_end_block = event["args"]["voteEnd"]
        description_full = event["args"]["description"]
        
        parts = description_full.split("0|||0")
        if len(parts) >= 4 : # Changed to >= 4 for robustness, allowing extra parts if any
            name = parts[0] if parts[0] else "(No Title Provided)"
            type_ = parts[1] if parts[1] else "unknown"
            desc = parts[2] if parts[2] else description_full # Fallback to full if parsing issue
            link = parts[3] if parts[3] else "(No Link Provided)"
        elif len(parts) == 1 and description_full: # If no delimiter, assume it's just a description
            name = description_full[:80] # Take first 80 chars as title
            type_ = "custom"
            desc = description_full
            link = "(No Link Provided)"
        else: # Fallback for other cases
            name = "(No Title Provided)"
            type_ = "unknown"
            desc = description_full if description_full else "(No Description Provided)"
            link = "(No Link Provided)"

        # Create Org object temporarily or fetch minimal data if needed by Proposal
        # For now, Proposal constructor takes org address (self.dao) which is a string
        p = Proposal(name=name, org=self.dao) # self.dao is the DAO address string
        p.author = proposer
        p.id = proposal_id # This might be redundant if proposal_id is used as document ID
        p.type = type_
        p.targets = targets
        p.values = values
        p.description = desc
        p.callDatas = calldatas # Storing as list of hex strings

        # Get totalSupply from the correct token contract associated with this DAO
        token_contract_for_dao = self.get_specific_contract(self.token_paper.address, tokenAbiGlobal)
        if token_contract_for_dao:
            try:
                p.totalSupply = str(token_contract_for_dao.functions.totalSupply().call())
            except Exception as e:
                print(f"Error fetching totalSupply for proposal {proposal_id} from token {self.token_paper.address}: {e}")
                p.totalSupply = "0" # Fallback
        else:
            p.totalSupply = "0"


        p.createdAt = datetime.now(tz=timezone.utc) # Event timestamp might be better if available & reliable
        p.votingStartsBlock = str(vote_start_block)
        p.votingEndsBlock = str(vote_end_block)
        p.externalResource = link
        
        proposal_doc_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id)
        try:
            proposal_doc_ref.set(p.toJson())

            member_doc_ref = self.daos_collection.document(self.dao).collection('members').document(proposer)
            # Ensure member exists before trying to update array union
            if member_doc_ref.get().exists:
                 member_doc_ref.update({"proposalsCreated": firestore.ArrayUnion([proposal_id])})
            else: # Create member if they proposed but weren't listed (e.g. just got tokens)
                print(f"Proposer {proposer} not found. Creating member entry.")
                # Try to get their balance for completeness
                balance = "0"
                if token_contract_for_dao:
                    try:
                        balance = str(token_contract_for_dao.functions.balanceOf(proposer).call())
                    except: pass # Ignore if balance fetch fails
                new_member = Member(address=proposer, personalBalance=balance, delegate="", votingWeight="0")
                new_member.proposalsCreated = [proposal_id]
                member_doc_ref.set(new_member.toJson())

        except Exception as e:
            print(f"Error saving proposal {proposal_id} or updating member {proposer} in DAO {self.dao}: {e}")


    def vote(self, log):
        # ... (vote logic should be mostly fine, ensure self.dao is correct) ...
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
        # dao_address = Web3.to_checksum_address(event['address']) # This is the DAO address
        tx_hash_bytes = event['transactionHash'] # transactionHash is typically bytes
        tx_hash_hex = tx_hash_bytes.hex()


        voter = Web3.to_checksum_address(event["args"]["voter"])
        support = event["args"]["support"] # 0=Against, 1=For, 2=Abstain (OpenZeppelin standard)
        weight = event["args"]["weight"]
        reason = event["args"]["reason"]
        
        vote_obj = Vote(proposalID=proposal_id, votingPower=str(weight), option=support, voter=voter)
        vote_obj.reason = reason
        vote_obj.hash = tx_hash_hex # Store tx hash
        
        # Firestore path construction
        proposal_votes_collection_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id).collection("votes")
        vote_doc_ref = proposal_votes_collection_ref.document(voter) # Use voter address as doc ID for their vote on this proposal

        batch = self.db.batch()
        batch.set(vote_doc_ref, vote_obj.toJson())

        member_doc_ref = self.daos_collection.document(self.dao).collection('members').document(voter)
        # Ensure member exists
        if member_doc_ref.get().exists:
            batch.update(member_doc_ref, {"proposalsVoted": firestore.ArrayUnion([proposal_id])})
        else:
            print(f"Voter {voter} not found. Creating member entry for vote.")
            balance = "0"
            if self.token_paper and self.token_paper.address: # Check if token_paper is set for the DAO
                token_contract_for_dao = self.get_specific_contract(self.token_paper.address, tokenAbiGlobal)
                if token_contract_for_dao:
                    try:
                        balance = str(token_contract_for_dao.functions.balanceOf(voter).call())
                    except: pass
            new_member = Member(address=voter, personalBalance=balance, delegate="", votingWeight="0")
            new_member.proposalsVoted = [proposal_id]
            batch.set(member_doc_ref, new_member.toJson())
            
        proposal_doc_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id)
        
        # Transactional update for proposal vote counts
        @firestore.transactional
        def update_proposal_votes(transaction, proposal_ref, weight_val, support_val):
            proposal_snapshot = proposal_ref.get(transaction=transaction)
            if not proposal_snapshot.exists:
                print(f"Proposal {proposal_id} not found during vote update transaction.")
                return

            prop_data = proposal_snapshot.to_dict()
            
            current_in_favor = int(prop_data.get('inFavor', "0"))
            current_against = int(prop_data.get('against', "0"))
            # Abstain not explicitly handled by current_in_favor/against in OZ Governor unless extended for it
            
            current_votes_for = prop_data.get('votesFor', 0)
            current_votes_against = prop_data.get('votesAgainst', 0)

            if support_val == 1: # For
                new_in_favor = current_in_favor + weight_val
                transaction.update(proposal_ref, {
                    'inFavor': str(new_in_favor),
                    'votesFor': current_votes_for + 1
                })
            elif support_val == 0: # Against
                new_against = current_against + weight_val
                transaction.update(proposal_ref, {
                    'against': str(new_against),
                    'votesAgainst': current_votes_against + 1
                })
            # Add logic for abstain (support == 2) if your governor supports it and you track it

        try:
            transaction = self.db.transaction()
            update_proposal_votes(transaction, proposal_doc_ref, int(weight), support)
            transaction.commit()
            batch.commit() # Commit the member update and vote document
            print(f"Vote by {voter} on proposal {proposal_id} processed.")
        except Exception as e:
            print(f"Error during vote processing for proposal {proposal_id} by {voter}: {e}")


    def queue(self, log):
        # ... (queue logic should be mostly fine, ensure self.dao is correct) ...
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
        eta = event['args']['eta'] # Execution timestamp
        
        proposal_doc_ref = self.daos_collection.document(self.dao).collection('proposals').document(proposal_id)
        
        # Convert ETA (timestamp) to datetime object
        execution_datetime = datetime.fromtimestamp(eta, tz=timezone.utc)
        
        try:
            proposal_doc_ref.update({
                "statusHistory.queued": datetime.now(tz=timezone.utc), # Time of queuing
                "latestStage": "Queued", # Assuming ProposalStatus enum has Queued
                "executionStarts": execution_datetime # Store the ETA
            })
            print(f"Proposal {proposal_id} queued in DAO {self.dao}, ETA: {execution_datetime}.")
        except Exception as e:
            print(f"Error updating proposal {proposal_id} on queue event in DAO {self.dao}: {e}")


    def bytes_to_int(self, byte_array):
        # ... (your existing helper) ...
        return int.from_bytes(byte_array, byteorder='big')

    def decode_params(self, data_bytes_hex): # Expects hex string like "0x..."
        # ... (your existing helper, ensure it handles hex string input correctly) ...
        if not isinstance(data_bytes_hex, str) or not data_bytes_hex.startswith("0x"):
            print(f"decode_params expects a hex string starting with 0x, got {data_bytes_hex}")
            return None, None
        try:
            data_bytes = bytes.fromhex(data_bytes_hex[2:]) # Remove "0x" and convert to bytes
        except ValueError as e:
            print(f"Error converting hex to bytes in decode_params: {data_bytes_hex}, error: {e}")
            return None, None

        data_without_selector = data_bytes[4:]
        if len(data_without_selector) < 64: # Not enough data for two offsets
            print("Not enough data for two offsets in decode_params")
            return None,None

        param1_offset_bytes = data_without_selector[:32]
        param2_offset_bytes = data_without_selector[32:64]
        param1_offset = self.bytes_to_int(param1_offset_bytes)
        param2_offset = self.bytes_to_int(param2_offset_bytes)

        # Adjust offsets: they are relative to the start of data_without_selector for dynamic part
        # The actual start of dynamic data section is after the static part (offsets)
        # For two string params, the static part is 64 bytes (two offsets).
        # So, offsets are from the beginning of data_without_selector.

        # Param1 decoding
        if param1_offset + 32 > len(data_without_selector):
             print("Param1 offset out of bounds")
             return None,None
        param1_length_bytes = data_without_selector[param1_offset : param1_offset + 32]
        param1_length = self.bytes_to_int(param1_length_bytes)
        if param1_offset + 32 + param1_length > len(data_without_selector):
            print("Param1 length out of bounds")
            return None, None
        param1_data_bytes = data_without_selector[param1_offset + 32 : param1_offset + 32 + param1_length]
        param1_data = param1_data_bytes.decode('utf-8', errors='replace') # Add error handling for decode

        # Param2 decoding
        if param2_offset + 32 > len(data_without_selector):
            print("Param2 offset out of bounds")
            return None,None
        param2_length_bytes = data_without_selector[param2_offset : param2_offset + 32]
        param2_length = self.bytes_to_int(param2_length_bytes)
        if param2_offset + 32 + param2_length > len(data_without_selector):
            print("Param2 length out of bounds")
            return None, None
        param2_data_bytes = data_without_selector[param2_offset + 32 : param2_offset + 32 + param2_length]
        param2_data = param2_data_bytes.decode('utf-8', errors='replace') # Add error handling for decode
        
        return param1_data, param2_data

     
    def execute(self, log):
        # ... (execute logic, ensure self.dao is correct and robust fetching/updates) ...
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
            # Optionally, create a basic proposal entry if this can happen legitimately
            # For now, we assume proposal should exist.
            return

        prop_data_from_db = proposal_snapshot.to_dict()
        # Create a Proposal object and populate it from DB data
        # This requires Proposal class to have a fromJson method or similar hydration logic
        # For now, directly updating fields for simplicity:
        
        updates_for_proposal = {
            "statusHistory.executed": datetime.now(tz=timezone.utc),
            "latestStage": "Executed", # Assuming ProposalStatus enum has Executed
            "executionHash": event['transactionHash'].hex()
        }

        # Update DAO state based on proposal type
        # Using prop_data_from_db which is the dictionary from Firestore
        proposal_type = prop_data_from_db.get('type', "").lower()
        proposal_calldatas = prop_data_from_db.get('callDatas', []) # Ensure this is a list of hex strings
        proposal_targets_db = prop_data_from_db.get('targets', []) # Ensure this is a list of addresses

        dao_doc_ref = self.daos_collection.document(self.dao)
        dao_updates = {}

        try:
            if "voting period" in proposal_type and proposal_calldatas:
                decoded = decode_function_parameters(voting_period_function_abi, proposal_calldatas[0])
                if decoded and len(decoded) > 0:
                    new_voting_period_seconds = int(decoded[0]) # Assuming it's in seconds as per OZ Governor
                    # Your app seems to store it as minutes or blocks, adjust accordingly.
                    # If storing as blocks, this might need block time estimation.
                    # If storing as minutes: new_voting_period_minutes = new_voting_period_seconds // 60
                    # For now, storing what's decoded (likely seconds or blocks based on contract)
                    dao_updates["votingDuration"] = new_voting_period_seconds 
                    print(f"DAO {self.dao} voting period updated to {new_voting_period_seconds}")

            if "threshold" in proposal_type and proposal_calldatas:
                decoded = decode_function_parameters(proposal_threshold_function_abi, proposal_calldatas[0])
                if decoded and len(decoded) > 0:
                    new_raw_threshold = int(decoded[0])
                    # Fetch current DAO decimals to correctly interpret the threshold
                    dao_snapshot = dao_doc_ref.get()
                    if dao_snapshot.exists:
                        current_dao_data = dao_snapshot.to_dict()
                        decimals = int(current_dao_data.get('decimals', 18)) # Default to 18 if not found
                        # new_proposal_threshold_adjusted = new_raw_threshold # If storing raw value
                        new_proposal_threshold_adjusted = str(new_raw_threshold // (10**decimals)) # If storing adjusted value
                        dao_updates["proposalThreshold"] = str(new_proposal_threshold_adjusted)
                        print(f"DAO {self.dao} proposal threshold updated to {new_proposal_threshold_adjusted} (adjusted from raw {new_raw_threshold})")
                    else:
                        print(f"Could not fetch DAO data to adjust proposal threshold for DAO {self.dao}")
                        dao_updates["proposalThreshold"] = str(new_raw_threshold) # Store raw if DAO data fetch fails

            if "delay" in proposal_type and proposal_calldatas: # This usually refers to votingDelay
                decoded = decode_function_parameters(voting_delay_function_abi, proposal_calldatas[0])
                if decoded and len(decoded) > 0:
                    new_voting_delay_value = int(decoded[0]) # Usually blocks for Governor votingDelay
                    dao_updates["votingDelay"] = new_voting_delay_value
                    print(f"DAO {self.dao} voting delay updated to {new_voting_delay_value}")
            
            if "timelock delay" in proposal_type and proposal_calldatas: # If changing Timelock's minDelay
                # This would require target to be Timelock and different ABI
                # Assuming 'delay' above refers to Governor's votingDelay for now.
                # If it's for TimelockController's minDelay:
                # decoded = decode_function_parameters(timelock_min_delay_abi_for_set, proposal_calldatas[0])
                # dao_updates["executionDelay"] = int(decoded[0])
                pass


            if "quorum" in proposal_type and proposal_calldatas:
                decoded = decode_function_parameters(quorum_function_abi, proposal_calldatas[0])
                if decoded and len(decoded) > 0:
                    dao_updates["quorum"] = int(decoded[0]) # This is quorumNumerator (percentage for fraction)
                    print(f"DAO {self.dao} quorum updated to {int(decoded[0])}")

            if proposal_type == "registry" and proposal_calldatas:
                # Your decode_params expects a hex string. Ensure proposal_calldatas[0] is that.
                key, value = self.decode_params(proposal_calldatas[0])
                if key is not None and value is not None:
                    # Atomically update the registry map
                    # This requires registry to be a map field in Firestore.
                    # registry_update_key = f"registry.{key}" # Firestore path for map update
                    # dao_updates[registry_update_key] = value
                    # More robust: fetch, update, set
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
                    # Determine if mint or burn from function selector or a more reliable field in prop_data_from_db
                    # Assuming mint_function_abi is for mint, burn_function_abi for burn
                    # This part needs to be more robust to distinguish mint vs burn.
                    # For now, assuming mint_function_abi for decoding.
                    try:
                        params = decode_function_parameters(function_abi=mint_function_abi, data_bytes=proposal_calldatas[0])
                        if params and len(params) > 0:
                            member_address_affected = Web3.to_checksum_address(params[0]) # Usually 'to' for mint, 'from' for burn
                            
                            # Update member balance
                            new_balance = target_token_contract.functions.balanceOf(member_address_affected).call()
                            member_doc_ref = self.daos_collection.document(self.dao).collection('members').document(member_address_affected)
                            if member_doc_ref.get().exists:
                                member_doc_ref.update({"personalBalance": str(new_balance)})
                            else: # Create member if they received tokens but weren't listed
                                print(f"Member {member_address_affected} not found for mint/burn. Creating.")
                                new_member = Member(address=member_address_affected, personalBalance=str(new_balance), delegate="", votingWeight="0")
                                member_doc_ref.set(new_member.toJson())
                            print(f"Member {member_address_affected} balance updated to {new_balance} after mint/burn.")

                            # Update DAO total supply
                            new_total_supply = target_token_contract.functions.totalSupply().call()
                            dao_updates["totalSupply"] = str(new_total_supply)
                            print(f"DAO {self.dao} total supply updated to {new_total_supply} after mint/burn.")
                    except Exception as e:
                        print(f"Error decoding/processing mint/burn params for proposal {proposal_id}: {e}")
                else:
                    print(f"Could not get contract for target token {token_address_target} in mint/burn.")
            
            # Commit updates
            if dao_updates:
                dao_doc_ref.update(dao_updates)
            proposal_doc_ref.update(updates_for_proposal)
            print(f"Proposal {proposal_id} execution processed for DAO {self.dao}.")

        except Exception as e:
            import traceback
            print(f"Error during proposal execution processing for prop {proposal_id}, DAO {self.dao}: {e}")
            print(traceback.format_exc())


    def handle_event(self, log, func=None):
        if self.kind == "wrapper": # Original WrapperContract
            if func == "NewDaoCreated":
                return self.add_dao(log)
        elif self.kind == "wrapper_w": # New WrapperContract_W
            if func == "DaoWrappedDeploymentInfo": # Match the event name from app.py
                return self.add_dao_wrapped(log)
        elif self.kind == "token":
            if func == "DelegateChanged": # Assuming only DelegateChanged for tokens for now
                self.delegate(log)
            # Add other token events if needed (e.g., Transfer for balance updates if not mint/burn proposals)
        elif self.kind == "dao":
            if func == "ProposalCreated":
                self.propose(log)
            elif func == "VoteCast":
                self.vote(log)
            elif func == "ProposalQueued":
                self.queue(log)
            elif func == "ProposalExecuted":
                self.execute(log)
            # Add handlers for other DAO events like ProposalCanceled if needed
        return None # Default return if no specific handler matched
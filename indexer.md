# Project: indexer

## Folder Structure

- indexer/
  - app.py
  - apps/
    - homebase/
      - abis.py
      - entities.py
      - eventSignatures.py
      - paper.py

## File Contents

### `app.py`
```py
from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal # wrapper_w_abi might be needed if different
from apps.homebase.paper import Paper
from datetime import datetime, timezone
import time
from firebase_admin import initialize_app
from firebase_admin import firestore, credentials
from web3 import Web3
import os
import sys
import re

# ... (Firebase initialization and RPC setup as before) ...
cred = credentials.Certificate('homebase.json')
initialize_app(cred)
db = firestore.client()
networks = db.collection("contracts")
ceva = networks.document("Etherlink-Testnet").get() # Or your relevant network document
rpc = "https://node.ghostnet.etherlink.com" # Or your RPC
wrapper_address = ceva.to_dict()['wrapper'] # This is your original WrapperContract address
wrapper_w_address = ceva.to_dict()['wrapper_w'] # <<< ADD THIS: Address of deployed WrapperContract_W

print("Original Wrapper address :" + str(wrapper_address))
print("Wrapped Token Wrapper address :" + str(wrapper_w_address)) # <<< For logging

web3 = Web3(Web3.HTTPProvider(rpc))
papers = {}
daos = []
if web3.is_connected():
    print("node connected")
else:
    print("node connection failed!")

daos_collection = db.collection('idaosEtherlink-Testnet') # Or your relevant collection name
docs = list(daos_collection.stream())
dao_addresses = [doc.id for doc in docs]

for doc in docs:
    obj = doc.to_dict()
    try:
        # This part is for re-hydrating existing DAOs, might need adjustments if token/dao kinds vary
        p = Paper(address=obj['token'], kind="token", 
                  daos_collection=daos_collection, db=db,  web3=web3, dao=doc.id)
        dao = Paper(address=obj['address'], kind="dao", token=p, 
                    daos_collection=daos_collection, db=db,  web3=web3, dao=doc.id)
    except Exception as e:
        print(f"one DAO contract can't parse correctly for {doc.id}: {e}") # More specific error
    papers.update({obj['token']: p})
    papers.update({obj['address']: dao})

# Calculate Keccak hash for the new event
# DaoWrappedDeploymentInfo(address,address,address,string,string,string,uint8)
# Note: The `indexed` keyword doesn't change the types in the signature string for keccak.
event_text_wrapped_dao = "DaoWrappedDeploymentInfo(address,address,address,string,string,string,uint8)"
keccak_hash_wrapped_dao = web3.keccak(text=event_text_wrapped_dao).hex()
print(f"Keccak hash for DaoWrappedDeploymentInfo: {keccak_hash_wrapped_dao}")


event_signatures = {
    web3.keccak(text="NewDaoCreated(address,address,address[],uint256[],string,string,string,uint256,address,string[],string[])").hex(): "NewDaoCreated",
    # "0x01c5013cf023a364cc49643b8f57347e398d2f0db0968edeb64e7c41bf2dfbde": "NewDaoCreated", # This seems like a hardcoded hash, ensure it matches your actual event if used
    keccak_hash_wrapped_dao: "DaoWrappedDeploymentInfo", # <<< ADD THIS
    "0x3134e8a2e6d97e929a7e54011ea5485d7d196dd5f0ba4d4ef95803e8e3fc257f": "DelegateChanged",
    "0x7d84a6263ae0d98d3329bd7b46bb4e8d6f98cd35a7adb45c274c8b7fd5ebd5e0": "ProposalCreated",
    "0x9a2e42fd6722813d69113e7d0079d3d940171428df7373df9c7f7617cfda2892": "ProposalQueued",
    "0x712ae1383f79ac853f8d882153778e0260ef8f03b504e2866e0593e04d2b291f": "ProposalExecuted",
    "0xb8e138887d0aa13bab447e82de9d5c1777041ecd21ca36ba824ff1e6c07ddda4": "VoteCast"
}

# Assuming wrapper_t_address was a typo and you meant wrapper_w_address
papers.update({wrapper_address: Paper(address=wrapper_address, 
              kind="wrapper", daos_collection=daos_collection, db=db,  web3=web3)})
papers.update({wrapper_w_address: Paper(address=wrapper_w_address, # <<< ADD THIS (using wrapper_w_address)
              kind="wrapper_w", daos_collection=daos_collection, db=db,  web3=web3)}) # <<< Changed kind to "wrapper_w"

listening_to_addresses = [wrapper_address, wrapper_w_address] # <<< ADD wrapper_w_address
listening_to_addresses = listening_to_addresses + list(dao_addresses) # Only listen to DAO addresses initially, tokens will be added as DAOs are processed

# Filter out None from listening_to_addresses if any DAO processing failed earlier
listening_to_addresses = [addr for addr in listening_to_addresses if addr is not None]
# Add already known token addresses
known_token_addresses = [paper_obj.address for addr, paper_obj in papers.items() if paper_obj.kind == "token" and paper_obj.address is not None]
listening_to_addresses.extend(known_token_addresses)
listening_to_addresses = list(set(listening_to_addresses)) # Remove duplicates


counter = 0
processed_transactions = set()
heartbeat = 0
print(f"Listening for {len(event_signatures)} events on {len(listening_to_addresses)} contracts: {listening_to_addresses}")


while True:
    heartbeat += 1
    try:
        latest = web3.eth.block_number
        # Consider a smaller range if your node struggles, or a persistent `first` block
        first = latest - 13 if latest > 13 else 0 
        
        current_block_to_process = first # For logging purposes

        # It's safer to query logs for one block at a time if the range is too large
        # or if you expect many events.
        # For simplicity, keeping your range logic for now.
        # print(f"Fetching logs from block {first} to {latest}")
        
        logs = web3.eth.get_logs({
            "fromBlock": first,
            "toBlock": latest,
            "address": listening_to_addresses,
            # Topics are useful for pre-filtering by the node, but your current approach filters in Python
            # "topics": [[*event_signatures.keys()]] # This would mean only these event topics
        })

        if logs:
            print(f"Found {len(logs)} logs between block {first} and {latest}")

        for log_entry in logs: # Renamed log to log_entry to avoid conflict with `import logging`
            tx_hash = log_entry["transactionHash"].hex()
            if tx_hash in processed_transactions:
                # print(f"Skipping already processed tx: {tx_hash}") # Verbose
                continue 
            
            contract_address = log_entry["address"]
            # Ensure topics are not empty and log_entry["topics"][0] exists
            if not log_entry["topics"]:
                print(f"Log entry with no topics: {log_entry}")
                continue
            
            event_signature_from_log = log_entry["topics"][0].hex() # No "0x" prefix needed for dict key
            
            event_name = event_signatures.get(event_signature_from_log)

            if event_name:
                print(f"Event: {event_name}, Contract: {contract_address}, Tx: {tx_hash}")
                processed_transactions.add(tx_hash) # Add to processed only if we handle it
            else:
                # print(f"Event signature not found in map: {event_signature_from_log}") # Verbose
                continue # Skip unknown events early
            
            # Ensure the paper object exists for the contract address
            if contract_address not in papers:
                print(f"Paper object not found for contract address: {contract_address}. Skipping event.")
                continue

            new_contract_addresses = papers[contract_address].handle_event(
                log_entry, func=event_name) # Pass log_entry
            
            if new_contract_addresses: # Check if it's not None and not empty
                dao_address_new = new_contract_addresses[0]
                token_address_new = new_contract_addresses[1]
                
                if dao_address_new and token_address_new: # Ensure both are valid
                    print(f"Adding new DAO {dao_address_new} and Token {token_address_new} to listen list and papers.")
                    
                    if dao_address_new not in listening_to_addresses:
                        listening_to_addresses.append(dao_address_new)
                    if token_address_new not in listening_to_addresses:
                        listening_to_addresses.append(token_address_new)
                    
                    # Create Paper object for the new token
                    # Check if token paper already exists to avoid overwriting
                    if token_address_new not in papers:
                        p_new_token = Paper(address=token_address_new, kind="token",
                                            daos_collection=daos_collection, db=db, dao=dao_address_new, web3=web3)
                        papers.update({token_address_new: p_new_token})
                    else:
                        p_new_token = papers[token_address_new] # Use existing if any (e.g. underlying token)
                    
                    # Create Paper object for the new DAO
                    if dao_address_new not in papers:
                        papers.update({dao_address_new: Paper(token=p_new_token, # Pass the token's Paper object
                                                          address=dao_address_new, kind="dao", 
                                                          daos_collection=daos_collection, db=db, dao=dao_address_new, web3=web3)})
                    print(f"Now listening to {len(listening_to_addresses)} addresses.")
                else:
                    print("handle_event returned None or invalid addresses for DAO/Token creation.")

    except Exception as e:
        # More detailed error logging
        import traceback
        print(f"MAIN LOOP ERROR: {e}")
        print(traceback.format_exc())
        # Reconnect logic
        try:
            web3 = Web3(Web3.HTTPProvider(rpc))
            if web3.is_connected():
                print("Node reconnected successfully.")
            else:
                print("Node reconnection failed! Attempting to restart script.")
                # Consider a more graceful exit or retry mechanism than immediate restart
                # os.execl(sys.executable, sys.executable, *sys.argv) # This can lead to restart loops
        except Exception as recon_e:
            print(f"Error during reconnection: {recon_e}")
            
    if heartbeat % 50 == 0: # Print heartbeat less often
        print(f"Heartbeat: {heartbeat}. Listening to {len(listening_to_addresses)} addresses. Processed {len(processed_transactions)} unique txs.")

    time.sleep(5) # Slightly longer sleep
```

### `apps/homebase/abis.py`
```py
# ... (existing ABIs for setX functions) ...

# ABIs for GETTING current DAO settings (from Governor and Timelock)
# These are simplified examples; ensure they match your actual contract interfaces.

# From Governor (e.g., HomebaseDAO which inherits GovernorSettings)
governor_proposal_threshold_abi = {
    "name": "proposalThreshold",
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}],
}

governor_voting_delay_abi = {
    "name": "votingDelay", # in blocks
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}], # OZ Governor returns uint256 for blocks
}

governor_voting_period_abi = {
    "name": "votingPeriod", # in blocks
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}], # OZ Governor returns uint256 for blocks
}

# From Governor (GovernorTimelockControl)
governor_timelock_abi = {
    "name": "timelock", # Gets the address of the TimelockController
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "address"}],
}

# From TimelockController
timelock_min_delay_abi = {
    "name": "getMinDelay", # Timelock's execution delay in seconds
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}],
}

# From ERC20Wrapper (HBEVM_Wrapped_Token)
erc20_wrapper_underlying_abi = {
    "name": "underlying",
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "address"}],
}

# Keep your existing ABIs for setX functions if they are used by decode_function_parameters
quorum_function_abi = { # This is for SETTING quorum (updateQuorumNumerator usually)
    "name": "setQuorumNumerator", # Or updateQuorumNumerator if that's the actual function in GovernorVotesQuorumFraction
    "inputs": [
        {"name": "newQuorumNumerator", "type": "uint256"},
    ],
}

voting_period_function_abi = { # This is for SETTING
    "name": "setVotingPeriod",
    "inputs": [
        {"name": "newVotingPeriod", "type": "uint32"}, # In blocks for OZ Governor
    ],
}

proposal_threshold_function_abi = { # This is for SETTING
    "name": "setProposalThreshold",
    "inputs": [
        {"name": "newProposalThreshold", "type": "uint256"},
    ],
}
voting_delay_function_abi = { # This is for SETTING
    "name": "setVotingDelay",
    "inputs": [
        {"name": "newVotingDelay", "type": "uint48"}, # In blocks for OZ Governor
    ],
}


wrapperAbi = '''
[
	{
		"inputs": [
			{
				"components": [
					{
						"internalType": "string",
						"name": "name",
						"type": "string"
					},
					{
						"internalType": "string",
						"name": "symbol",
						"type": "string"
					},
					{
						"internalType": "string",
						"name": "description",
						"type": "string"
					},
					{
						"internalType": "uint8",
						"name": "decimals",
						"type": "uint8"
					},
					{
						"internalType": "uint256",
						"name": "executionDelay",
						"type": "uint256"
					},
					{
						"internalType": "address[]",
						"name": "initialMembers",
						"type": "address[]"
					},
					{
						"internalType": "uint256[]",
						"name": "initialAmounts",
						"type": "uint256[]"
					},
					{
						"internalType": "string[]",
						"name": "keys",
						"type": "string[]"
					},
					{
						"internalType": "string[]",
						"name": "values",
						"type": "string[]"
					}
				],
				"internalType": "struct WrapperContract.DaoParams",
				"name": "params",
				"type": "tuple"
			}
		],
		"name": "deployDAOwithToken",
		"outputs": [],
		"stateMutability": "payable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "_tokenFactory",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "_timelockFactory",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "_daoFactory",
				"type": "address"
			}
		],
		"stateMutability": "nonpayable",
		"type": "constructor"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "dao",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "address",
				"name": "token",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "address[]",
				"name": "initialMembers",
				"type": "address[]"
			},
			{
				"indexed": false,
				"internalType": "uint256[]",
				"name": "initialAmounts",
				"type": "uint256[]"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "name",
				"type": "string"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "symbol",
				"type": "string"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "description",
				"type": "string"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "executionDelay",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "address",
				"name": "registry",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "string[]",
				"name": "keys",
				"type": "string[]"
			},
			{
				"indexed": false,
				"internalType": "string[]",
				"name": "values",
				"type": "string[]"
			}
		],
		"name": "NewDaoCreated",
		"type": "event"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedDAOs",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedRegistries",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedTimelocks",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedTokens",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "getNumberOfDAOs",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	}
]
'''

daoAbiGlobal = '''[
	{
		"inputs": [
			{
				"internalType": "contract IVotes",
				"name": "_token",
				"type": "address"
			},
			{
				"internalType": "contract TimelockController",
				"name": "_timelock",
				"type": "address"
			},
			{
				"internalType": "string",
				"name": "name",
				"type": "string"
			},
			{
				"internalType": "uint48",
				"name": "minsDelay",
				"type": "uint48"
			},
			{
				"internalType": "uint32",
				"name": "minsVoting",
				"type": "uint32"
			},
			{
				"internalType": "uint256",
				"name": "pThreshold",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "qvrm",
				"type": "uint8"
			}
		],
		"stateMutability": "nonpayable",
		"type": "constructor"
	},
	{
		"inputs": [],
		"name": "CheckpointUnorderedInsertion",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "FailedInnerCall",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "voter",
				"type": "address"
			}
		],
		"name": "GovernorAlreadyCastVote",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "GovernorAlreadyQueuedProposal",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "GovernorDisabledDeposit",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "proposer",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "votes",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "threshold",
				"type": "uint256"
			}
		],
		"name": "GovernorInsufficientProposerVotes",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "targets",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "calldatas",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "values",
				"type": "uint256"
			}
		],
		"name": "GovernorInvalidProposalLength",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "quorumNumerator",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "quorumDenominator",
				"type": "uint256"
			}
		],
		"name": "GovernorInvalidQuorumFraction",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "voter",
				"type": "address"
			}
		],
		"name": "GovernorInvalidSignature",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "GovernorInvalidVoteType",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "votingPeriod",
				"type": "uint256"
			}
		],
		"name": "GovernorInvalidVotingPeriod",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "GovernorNonexistentProposal",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "GovernorNotQueuedProposal",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			}
		],
		"name": "GovernorOnlyExecutor",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			}
		],
		"name": "GovernorOnlyProposer",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "GovernorQueueNotImplemented",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "proposer",
				"type": "address"
			}
		],
		"name": "GovernorRestrictedProposer",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"internalType": "enum IGovernor.ProposalState",
				"name": "current",
				"type": "uint8"
			},
			{
				"internalType": "bytes32",
				"name": "expectedStates",
				"type": "bytes32"
			}
		],
		"name": "GovernorUnexpectedProposalState",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "currentNonce",
				"type": "uint256"
			}
		],
		"name": "InvalidAccountNonce",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "InvalidShortString",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "QueueEmpty",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "QueueFull",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint8",
				"name": "bits",
				"type": "uint8"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "SafeCastOverflowedUintDowncast",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "string",
				"name": "str",
				"type": "string"
			}
		],
		"name": "StringTooLong",
		"type": "error"
	},
	{
		"anonymous": false,
		"inputs": [],
		"name": "EIP712DomainChanged",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "ProposalCanceled",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "address",
				"name": "proposer",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "address[]",
				"name": "targets",
				"type": "address[]"
			},
			{
				"indexed": false,
				"internalType": "uint256[]",
				"name": "values",
				"type": "uint256[]"
			},
			{
				"indexed": false,
				"internalType": "string[]",
				"name": "signatures",
				"type": "string[]"
			},
			{
				"indexed": false,
				"internalType": "bytes[]",
				"name": "calldatas",
				"type": "bytes[]"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "voteStart",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "voteEnd",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "description",
				"type": "string"
			}
		],
		"name": "ProposalCreated",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "ProposalExecuted",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "etaSeconds",
				"type": "uint256"
			}
		],
		"name": "ProposalQueued",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "oldProposalThreshold",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "newProposalThreshold",
				"type": "uint256"
			}
		],
		"name": "ProposalThresholdSet",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "oldQuorumNumerator",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "newQuorumNumerator",
				"type": "uint256"
			}
		],
		"name": "QuorumNumeratorUpdated",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "address",
				"name": "oldTimelock",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "address",
				"name": "newTimelock",
				"type": "address"
			}
		],
		"name": "TimelockChange",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "voter",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint8",
				"name": "support",
				"type": "uint8"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "weight",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "reason",
				"type": "string"
			}
		],
		"name": "VoteCast",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "voter",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint8",
				"name": "support",
				"type": "uint8"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "weight",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "reason",
				"type": "string"
			},
			{
				"indexed": false,
				"internalType": "bytes",
				"name": "params",
				"type": "bytes"
			}
		],
		"name": "VoteCastWithParams",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "oldVotingDelay",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "newVotingDelay",
				"type": "uint256"
			}
		],
		"name": "VotingDelaySet",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "oldVotingPeriod",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "newVotingPeriod",
				"type": "uint256"
			}
		],
		"name": "VotingPeriodSet",
		"type": "event"
	},
	{
		"inputs": [],
		"name": "BALLOT_TYPEHASH",
		"outputs": [
			{
				"internalType": "bytes32",
				"name": "",
				"type": "bytes32"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "CLOCK_MODE",
		"outputs": [
			{
				"internalType": "string",
				"name": "",
				"type": "string"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "COUNTING_MODE",
		"outputs": [
			{
				"internalType": "string",
				"name": "",
				"type": "string"
			}
		],
		"stateMutability": "pure",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "EXTENDED_BALLOT_TYPEHASH",
		"outputs": [
			{
				"internalType": "bytes32",
				"name": "",
				"type": "bytes32"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address[]",
				"name": "targets",
				"type": "address[]"
			},
			{
				"internalType": "uint256[]",
				"name": "values",
				"type": "uint256[]"
			},
			{
				"internalType": "bytes[]",
				"name": "calldatas",
				"type": "bytes[]"
			},
			{
				"internalType": "bytes32",
				"name": "descriptionHash",
				"type": "bytes32"
			}
		],
		"name": "cancel",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "support",
				"type": "uint8"
			}
		],
		"name": "castVote",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "support",
				"type": "uint8"
			},
			{
				"internalType": "address",
				"name": "voter",
				"type": "address"
			},
			{
				"internalType": "bytes",
				"name": "signature",
				"type": "bytes"
			}
		],
		"name": "castVoteBySig",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "support",
				"type": "uint8"
			},
			{
				"internalType": "string",
				"name": "reason",
				"type": "string"
			}
		],
		"name": "castVoteWithReason",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "support",
				"type": "uint8"
			},
			{
				"internalType": "string",
				"name": "reason",
				"type": "string"
			},
			{
				"internalType": "bytes",
				"name": "params",
				"type": "bytes"
			}
		],
		"name": "castVoteWithReasonAndParams",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "support",
				"type": "uint8"
			},
			{
				"internalType": "address",
				"name": "voter",
				"type": "address"
			},
			{
				"internalType": "string",
				"name": "reason",
				"type": "string"
			},
			{
				"internalType": "bytes",
				"name": "params",
				"type": "bytes"
			},
			{
				"internalType": "bytes",
				"name": "signature",
				"type": "bytes"
			}
		],
		"name": "castVoteWithReasonAndParamsBySig",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "clock",
		"outputs": [
			{
				"internalType": "uint48",
				"name": "",
				"type": "uint48"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "eip712Domain",
		"outputs": [
			{
				"internalType": "bytes1",
				"name": "fields",
				"type": "bytes1"
			},
			{
				"internalType": "string",
				"name": "name",
				"type": "string"
			},
			{
				"internalType": "string",
				"name": "version",
				"type": "string"
			},
			{
				"internalType": "uint256",
				"name": "chainId",
				"type": "uint256"
			},
			{
				"internalType": "address",
				"name": "verifyingContract",
				"type": "address"
			},
			{
				"internalType": "bytes32",
				"name": "salt",
				"type": "bytes32"
			},
			{
				"internalType": "uint256[]",
				"name": "extensions",
				"type": "uint256[]"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address[]",
				"name": "targets",
				"type": "address[]"
			},
			{
				"internalType": "uint256[]",
				"name": "values",
				"type": "uint256[]"
			},
			{
				"internalType": "bytes[]",
				"name": "calldatas",
				"type": "bytes[]"
			},
			{
				"internalType": "bytes32",
				"name": "descriptionHash",
				"type": "bytes32"
			}
		],
		"name": "execute",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "payable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "timepoint",
				"type": "uint256"
			}
		],
		"name": "getVotes",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "timepoint",
				"type": "uint256"
			},
			{
				"internalType": "bytes",
				"name": "params",
				"type": "bytes"
			}
		],
		"name": "getVotesWithParams",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			},
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			}
		],
		"name": "hasVoted",
		"outputs": [
			{
				"internalType": "bool",
				"name": "",
				"type": "bool"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address[]",
				"name": "targets",
				"type": "address[]"
			},
			{
				"internalType": "uint256[]",
				"name": "values",
				"type": "uint256[]"
			},
			{
				"internalType": "bytes[]",
				"name": "calldatas",
				"type": "bytes[]"
			},
			{
				"internalType": "bytes32",
				"name": "descriptionHash",
				"type": "bytes32"
			}
		],
		"name": "hashProposal",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "pure",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "name",
		"outputs": [
			{
				"internalType": "string",
				"name": "",
				"type": "string"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "owner",
				"type": "address"
			}
		],
		"name": "nonces",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			},
			{
				"internalType": "uint256[]",
				"name": "",
				"type": "uint256[]"
			},
			{
				"internalType": "uint256[]",
				"name": "",
				"type": "uint256[]"
			},
			{
				"internalType": "bytes",
				"name": "",
				"type": "bytes"
			}
		],
		"name": "onERC1155BatchReceived",
		"outputs": [
			{
				"internalType": "bytes4",
				"name": "",
				"type": "bytes4"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			},
			{
				"internalType": "bytes",
				"name": "",
				"type": "bytes"
			}
		],
		"name": "onERC1155Received",
		"outputs": [
			{
				"internalType": "bytes4",
				"name": "",
				"type": "bytes4"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			},
			{
				"internalType": "bytes",
				"name": "",
				"type": "bytes"
			}
		],
		"name": "onERC721Received",
		"outputs": [
			{
				"internalType": "bytes4",
				"name": "",
				"type": "bytes4"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "proposalDeadline",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "proposalEta",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "proposalNeedsQueuing",
		"outputs": [
			{
				"internalType": "bool",
				"name": "",
				"type": "bool"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "proposalProposer",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "proposalSnapshot",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "proposalThreshold",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "proposalVotes",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "againstVotes",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "forVotes",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "abstainVotes",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address[]",
				"name": "targets",
				"type": "address[]"
			},
			{
				"internalType": "uint256[]",
				"name": "values",
				"type": "uint256[]"
			},
			{
				"internalType": "bytes[]",
				"name": "calldatas",
				"type": "bytes[]"
			},
			{
				"internalType": "string",
				"name": "description",
				"type": "string"
			}
		],
		"name": "propose",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address[]",
				"name": "targets",
				"type": "address[]"
			},
			{
				"internalType": "uint256[]",
				"name": "values",
				"type": "uint256[]"
			},
			{
				"internalType": "bytes[]",
				"name": "calldatas",
				"type": "bytes[]"
			},
			{
				"internalType": "bytes32",
				"name": "descriptionHash",
				"type": "bytes32"
			}
		],
		"name": "queue",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "blockNumber",
				"type": "uint256"
			}
		],
		"name": "quorum",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "quorumDenominator",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "timepoint",
				"type": "uint256"
			}
		],
		"name": "quorumNumerator",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "quorumNumerator",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "target",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			},
			{
				"internalType": "bytes",
				"name": "data",
				"type": "bytes"
			}
		],
		"name": "relay",
		"outputs": [],
		"stateMutability": "payable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "newProposalThreshold",
				"type": "uint256"
			}
		],
		"name": "setProposalThreshold",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint48",
				"name": "newVotingDelay",
				"type": "uint48"
			}
		],
		"name": "setVotingDelay",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint32",
				"name": "newVotingPeriod",
				"type": "uint32"
			}
		],
		"name": "setVotingPeriod",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "proposalId",
				"type": "uint256"
			}
		],
		"name": "state",
		"outputs": [
			{
				"internalType": "enum IGovernor.ProposalState",
				"name": "",
				"type": "uint8"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "bytes4",
				"name": "interfaceId",
				"type": "bytes4"
			}
		],
		"name": "supportsInterface",
		"outputs": [
			{
				"internalType": "bool",
				"name": "",
				"type": "bool"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "timelock",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "token",
		"outputs": [
			{
				"internalType": "contract IERC5805",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "newQuorumNumerator",
				"type": "uint256"
			}
		],
		"name": "updateQuorumNumerator",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "contract TimelockController",
				"name": "newTimelock",
				"type": "address"
			}
		],
		"name": "updateTimelock",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "version",
		"outputs": [
			{
				"internalType": "string",
				"name": "",
				"type": "string"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "votingDelay",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "votingPeriod",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"stateMutability": "payable",
		"type": "receive"
	}
]'''


tokenAbiGlobal = '''
[
	{
		"inputs": [
			{
				"internalType": "string",
				"name": "name",
				"type": "string"
			},
			{
				"internalType": "string",
				"name": "symbol",
				"type": "string"
			},
			{
				"internalType": "address[]",
				"name": "initialMembers",
				"type": "address[]"
			},
			{
				"internalType": "uint256[]",
				"name": "initialAmounts",
				"type": "uint256[]"
			}
		],
		"stateMutability": "nonpayable",
		"type": "constructor"
	},
	{
		"inputs": [],
		"name": "CheckpointUnorderedInsertion",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "ECDSAInvalidSignature",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "length",
				"type": "uint256"
			}
		],
		"name": "ECDSAInvalidSignatureLength",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "bytes32",
				"name": "s",
				"type": "bytes32"
			}
		],
		"name": "ECDSAInvalidSignatureS",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "increasedSupply",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "cap",
				"type": "uint256"
			}
		],
		"name": "ERC20ExceededSafeSupply",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "spender",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "allowance",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "needed",
				"type": "uint256"
			}
		],
		"name": "ERC20InsufficientAllowance",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "sender",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "balance",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "needed",
				"type": "uint256"
			}
		],
		"name": "ERC20InsufficientBalance",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "approver",
				"type": "address"
			}
		],
		"name": "ERC20InvalidApprover",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "receiver",
				"type": "address"
			}
		],
		"name": "ERC20InvalidReceiver",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "sender",
				"type": "address"
			}
		],
		"name": "ERC20InvalidSender",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "spender",
				"type": "address"
			}
		],
		"name": "ERC20InvalidSpender",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "deadline",
				"type": "uint256"
			}
		],
		"name": "ERC2612ExpiredSignature",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "signer",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "owner",
				"type": "address"
			}
		],
		"name": "ERC2612InvalidSigner",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "timepoint",
				"type": "uint256"
			},
			{
				"internalType": "uint48",
				"name": "clock",
				"type": "uint48"
			}
		],
		"name": "ERC5805FutureLookup",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "ERC6372InconsistentClock",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "currentNonce",
				"type": "uint256"
			}
		],
		"name": "InvalidAccountNonce",
		"type": "error"
	},
	{
		"inputs": [],
		"name": "InvalidShortString",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint8",
				"name": "bits",
				"type": "uint8"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "SafeCastOverflowedUintDowncast",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "string",
				"name": "str",
				"type": "string"
			}
		],
		"name": "StringTooLong",
		"type": "error"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "expiry",
				"type": "uint256"
			}
		],
		"name": "VotesExpiredSignature",
		"type": "error"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "owner",
				"type": "address"
			},
			{
				"indexed": true,
				"internalType": "address",
				"name": "spender",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "Approval",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "delegator",
				"type": "address"
			},
			{
				"indexed": true,
				"internalType": "address",
				"name": "fromDelegate",
				"type": "address"
			},
			{
				"indexed": true,
				"internalType": "address",
				"name": "toDelegate",
				"type": "address"
			}
		],
		"name": "DelegateChanged",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "delegate",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "previousVotes",
				"type": "uint256"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "newVotes",
				"type": "uint256"
			}
		],
		"name": "DelegateVotesChanged",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [],
		"name": "EIP712DomainChanged",
		"type": "event"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "from",
				"type": "address"
			},
			{
				"indexed": true,
				"internalType": "address",
				"name": "to",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "Transfer",
		"type": "event"
	},
	{
		"inputs": [],
		"name": "CLOCK_MODE",
		"outputs": [
			{
				"internalType": "string",
				"name": "",
				"type": "string"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "DOMAIN_SEPARATOR",
		"outputs": [
			{
				"internalType": "bytes32",
				"name": "",
				"type": "bytes32"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "owner",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "spender",
				"type": "address"
			}
		],
		"name": "allowance",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "spender",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "approve",
		"outputs": [
			{
				"internalType": "bool",
				"name": "",
				"type": "bool"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			}
		],
		"name": "balanceOf",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint32",
				"name": "pos",
				"type": "uint32"
			}
		],
		"name": "checkpoints",
		"outputs": [
			{
				"components": [
					{
						"internalType": "uint48",
						"name": "_key",
						"type": "uint48"
					},
					{
						"internalType": "uint208",
						"name": "_value",
						"type": "uint208"
					}
				],
				"internalType": "struct Checkpoints.Checkpoint208",
				"name": "",
				"type": "tuple"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "clock",
		"outputs": [
			{
				"internalType": "uint48",
				"name": "",
				"type": "uint48"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "decimals",
		"outputs": [
			{
				"internalType": "uint8",
				"name": "",
				"type": "uint8"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "delegatee",
				"type": "address"
			}
		],
		"name": "delegate",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "delegatee",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "nonce",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "expiry",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "v",
				"type": "uint8"
			},
			{
				"internalType": "bytes32",
				"name": "r",
				"type": "bytes32"
			},
			{
				"internalType": "bytes32",
				"name": "s",
				"type": "bytes32"
			}
		],
		"name": "delegateBySig",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			}
		],
		"name": "delegates",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "eip712Domain",
		"outputs": [
			{
				"internalType": "bytes1",
				"name": "fields",
				"type": "bytes1"
			},
			{
				"internalType": "string",
				"name": "name",
				"type": "string"
			},
			{
				"internalType": "string",
				"name": "version",
				"type": "string"
			},
			{
				"internalType": "uint256",
				"name": "chainId",
				"type": "uint256"
			},
			{
				"internalType": "address",
				"name": "verifyingContract",
				"type": "address"
			},
			{
				"internalType": "bytes32",
				"name": "salt",
				"type": "bytes32"
			},
			{
				"internalType": "uint256[]",
				"name": "extensions",
				"type": "uint256[]"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "timepoint",
				"type": "uint256"
			}
		],
		"name": "getPastTotalSupply",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "timepoint",
				"type": "uint256"
			}
		],
		"name": "getPastVotes",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			}
		],
		"name": "getVotes",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "to",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "amount",
				"type": "uint256"
			}
		],
		"name": "mint",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "name",
		"outputs": [
			{
				"internalType": "string",
				"name": "",
				"type": "string"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "owner",
				"type": "address"
			}
		],
		"name": "nonces",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			}
		],
		"name": "numCheckpoints",
		"outputs": [
			{
				"internalType": "uint32",
				"name": "",
				"type": "uint32"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "owner",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "spender",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			},
			{
				"internalType": "uint256",
				"name": "deadline",
				"type": "uint256"
			},
			{
				"internalType": "uint8",
				"name": "v",
				"type": "uint8"
			},
			{
				"internalType": "bytes32",
				"name": "r",
				"type": "bytes32"
			},
			{
				"internalType": "bytes32",
				"name": "s",
				"type": "bytes32"
			}
		],
		"name": "permit",
		"outputs": [],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "symbol",
		"outputs": [
			{
				"internalType": "string",
				"name": "",
				"type": "string"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "totalSupply",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "to",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "transfer",
		"outputs": [
			{
				"internalType": "bool",
				"name": "",
				"type": "bool"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "from",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "to",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "transferFrom",
		"outputs": [
			{
				"internalType": "bool",
				"name": "",
				"type": "bool"
			}
		],
		"stateMutability": "nonpayable",
		"type": "function"
	}
]
'''
wrapper_w_abi = '''
[
	{
		"inputs": [
			{
				"internalType": "address",
				"name": "_tokenFactory",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "_timelockFactory",
				"type": "address"
			},
			{
				"internalType": "address",
				"name": "_daoFactory",
				"type": "address"
			}
		],
		"stateMutability": "nonpayable",
		"type": "constructor"
	},
	{
		"anonymous": false,
		"inputs": [
			{
				"indexed": true,
				"internalType": "address",
				"name": "daoAddress",
				"type": "address"
			},
			{
				"indexed": true,
				"internalType": "address",
				"name": "wrappedTokenAddress",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "address",
				"name": "registryAddress",
				"type": "address"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "daoName",
				"type": "string"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "wrappedTokenSymbol",
				"type": "string"
			},
			{
				"indexed": false,
				"internalType": "string",
				"name": "description",
				"type": "string"
			},
			{
				"indexed": false,
				"internalType": "uint8",
				"name": "quorumFraction",
				"type": "uint8"
			}
		],
		"name": "DaoWrappedDeploymentInfo",
		"type": "event"
	},
	{
		"inputs": [
			{
				"components": [
					{
						"internalType": "string",
						"name": "daoName",
						"type": "string"
					},
					{
						"internalType": "string",
						"name": "wrappedTokenSymbol",
						"type": "string"
					},
					{
						"internalType": "string",
						"name": "description",
						"type": "string"
					},
					{
						"internalType": "uint256",
						"name": "executionDelay",
						"type": "uint256"
					},
					{
						"internalType": "address",
						"name": "underlyingTokenAddress",
						"type": "address"
					},
					{
						"internalType": "uint48",
						"name": "minsVotingDelay",
						"type": "uint48"
					},
					{
						"internalType": "uint32",
						"name": "minsVotingPeriod",
						"type": "uint32"
					},
					{
						"internalType": "uint256",
						"name": "proposalThreshold",
						"type": "uint256"
					},
					{
						"internalType": "uint8",
						"name": "quorumFraction",
						"type": "uint8"
					},
					{
						"internalType": "string[]",
						"name": "keys",
						"type": "string[]"
					},
					{
						"internalType": "string[]",
						"name": "values",
						"type": "string[]"
					}
				],
				"internalType": "struct WrapperContract_W.DaoParamsWrapped",
				"name": "params",
				"type": "tuple"
			}
		],
		"name": "deployDAOwithWrappedToken",
		"outputs": [],
		"stateMutability": "payable",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedDAOs_W",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedRegistries_W",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedTimelocks_W",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"name": "deployedTokens_W",
		"outputs": [
			{
				"internalType": "address",
				"name": "",
				"type": "address"
			}
		],
		"stateMutability": "view",
		"type": "function"
	},
	{
		"inputs": [],
		"name": "getNumberOfDAOs_W",
		"outputs": [
			{
				"internalType": "uint256",
				"name": "",
				"type": "uint256"
			}
		],
		"stateMutability": "view",
		"type": "function"
	}
]
'''
```

### `apps/homebase/entities.py`
```py
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Union
from enum import Enum


class Member:
    def __init__(self, address, delegate, personalBalance, votingWeight) -> None:
        self.address = address
        self.delegate = delegate
        self.personalBalance = personalBalance
        self.constituents = []
        self.votingWeight = votingWeight
        self.proposalsVoted = []
        self.proposalsCreated = []

    def toJson(self):
        return {
            'address': self.address,
            'delegate': self.delegate,
            'personalBalance': str(self.personalBalance),
            'votingWeight': str(self.votingWeight),
            'constituents': self.constituents,
            'proposalsVoted': self.proposalsVoted,
            'proposalsCreated': self.proposalsCreated,
            'lastSeen': datetime.now(timezone.utc)
        }


class ProposalStatus(Enum):
    pending = 'pending'
    active = 'active'
    passed = 'passed'
    queued = 'queued'
    executable = 'executable'
    executed = 'executed'
    expired = 'expired'
    noQuorum = 'noQuorum'
    rejected = 'rejected'


class StateInContract(Enum):
    Pending = 0
    Active = 1
    Canceled = 2
    Defeated = 3
    Succeeded = 4
    Queued = 5
    Expired = 6
    Executed = 7


class Txaction:
    def toJson(self):
        pass  # Implement as needed


class Token:
    def __init__(self, name: str, symbol: str, decimals: Optional[int]):
        self.name: str = name
        self.symbol: str = symbol
        self.decimals: Optional[int] = decimals
        self.address: Optional[str] = None

    @classmethod
    def fromJson(cls, json_data: Dict[str, Union[str, int]]):
        name = json_data['name']
        symbol = json_data['symbol']
        decimals = json_data.get('decimals')
        address = json_data.get('address')
        token = cls(name=name, symbol=symbol, decimals=decimals)
        token.address = address
        return token

    def toJson(self):
        return {
            'name': self.name,
            'symbol': self.symbol,
            'decimals': self.decimals,
            'address': self.address,
        }


class Org:
    def __init__(self, name: str, govToken: Optional[Token] = None, description: Optional[str] = None, govTokenAddress: Optional[str] = None):
        self.pollsCollection = None
        self.votesCollection = None
        self.name = name
        self.govToken = govToken
        self.description = description
        self.govTokenAddress = govTokenAddress
        self.creationDate: Optional[datetime] = None
        self.memberAddresses: Dict[str, Member] = {}
        self.symbol: Optional[str] = None
        self.decimals: Optional[int] = None
        self.proposalThreshold: Optional[str] = 0
        self.totalSupply: Optional[str] = 0
        self.nonTransferrable: bool = False
        self.treasuryAddress: Optional[str] = None
        self.registryAddress: Optional[str] = None
        self.proposals: List['Proposal'] = []
        self.proposalIDs: Optional[List[str]] = []
        self.treasuryMap: Dict[str, str] = {}
        self.registry: Dict[str, str] = {}
        self.treasury: Dict[Token, str] = {}
        self.address: Optional[str] = None
        self.holders: int = 1
        self.quorum: int = 0
        self.votingDelay: int = 0
        self.votingDuration: int = 0
        self.nativeBalance: str = "0"
        self.executionDelay: int = 0

    def toJson(self):
        return {
            'name': self.name,
            'creationDate': self.creationDate,
            'description': self.description,
            'token': self.govTokenAddress,
            'treasuryAddress': self.treasuryAddress,
            'registryAddress': self.registryAddress,
            'address': self.address,
            'holders': self.holders,
            'symbol': self.symbol,
            'decimals': self.decimals,
            'proposals': self.proposalIDs,
            'proposalThreshold': str(self.proposalThreshold),
            'registry': self.registry if self.registry != None else {},
            'treasury': {token.toJson(): value for token, value in self.treasury.items()},
            'votingDelay': self.votingDelay,
            'totalSupply': self.totalSupply,
            'votingDuration': self.votingDuration,
            'executionDelay': self.executionDelay,
            'quorum': self.quorum,
            'nonTransferrable': self.nonTransferrable,
        }


class Proposal:
    def __init__(self, org: Org, name: Optional[str] = None):
        self.id: Optional[str] = ""
        self.inAppnumber: int = 0
        self.state: Optional[ProposalStatus] = None
        self.hash: str = ""
        self.totalSupply: str = "0"
        self.org: Org = org
        self.type: Optional[str] = None
        self.name: Optional[str] = name if name else "Title of the proposal (max 80 characters)"
        self.description: Optional[str] = "(no description)"
        self.author: Optional[str] = None
        self.value: float = 0.0
        self.targets: List[str] = []
        self.values: List[str] = []
        self.executionHash = ""
        self.callDatas: List = []
        self.callData: Optional[str] = "0x"
        self.createdAt: Optional[datetime] = datetime.now(timezone.utc)
        self.votingStarts: Optional[datetime] = None
        self.votingEnds: Optional[datetime] = None
        self.executionStarts: Optional[datetime] = None
        self.executionEnds: Optional[datetime] = None
        self.status: str = ""
        self.statusHistory: Dict[str, datetime] = {
            "pending": datetime.now(timezone.utc)}
        self.latestStage = "pending"
        self.turnoutPercent: int = 0
        self.votingStartsBlock: Optional[int] = None
        self.votingEndsBlock: Optional[int] = None
        self.executionStartsBlock: Optional[int] = None
        self.executionEndsBlock: Optional[int] = None
        self.inFavor: str = "0"
        self.against: str = "0"
        self.votesFor: int = 0
        self.votesAgainst: int = 0
        self.externalResource: Optional[str] = "(no link provided)"
        self.transactions: List[Txaction] = []
        self.votes: List['Vote'] = []

    def toJson(self):
        return {
            'hash': self.hash,
            'type': self.type,
            'title': self.name,
            'description': self.description,
            'author': self.author,
            'calldata': self.callData,
            'createdAt': self.createdAt,
            'callDatas': self.callDatas,
            'targets': self.targets,
            'totalSupply': self.totalSupply,
            'values': self.values,
            'executionHash': self.executionHash,
            'statusHistory': self.statusHistory,
            'turnoutPercent': self.turnoutPercent,
            'inFavor': self.inFavor,
            'against': self.against,
            'votesFor': self.votesFor,
            'latestStage': self.latestStage,
            'votesAgainst': self.votesAgainst,
            'externalResource': self.externalResource,
            'transactions': [tx.toJson() for tx in self.transactions],
        }

    def fromJson(self, firestore_data: Dict) -> None:
        """
        Method to populate the Proposal object from Firestore data.

        Args:
            firestore_data (Dict): The Firestore data as a dictionary.
        """
        self.id = firestore_data.get('id', "")
        self.state = firestore_data.get('state')
        self.hash = firestore_data.get('hash', "")
        self.type = firestore_data.get('type')
        self.totalSupply = firestore_data.get('totalSupply', "0")
        self.name = firestore_data.get('title', self.name)
        self.description = firestore_data.get('description', self.description)
        self.author = firestore_data.get('author')
        self.value = firestore_data.get('value', 0.0)
        self.targets = firestore_data.get('targets', [])
        self.values = firestore_data.get('values', [])
        self.callDatas = firestore_data.get('callDatas', [])
        self.callData = firestore_data.get('calldata', "0x")
        self.createdAt = firestore_data.get(
            'createdAt', datetime.now(timezone.utc))
        self.votingStarts = firestore_data.get('votingStarts')
        self.votingEnds = firestore_data.get('votingEnds')
        self.executionStarts = firestore_data.get('executionStarts')
        self.executionEnds = firestore_data.get('executionEnds')
        self.status = firestore_data.get('status', "")
        self.statusHistory = firestore_data.get(
            'statusHistory', {"pending": datetime.now(timezone.utc)})
        self.latestStage = firestore_data.get('latestStage', "pending")
        self.turnoutPercent = firestore_data.get('turnoutPercent', 0)
        self.votingStartsBlock = firestore_data.get('votingStartsBlock')
        self.votingEndsBlock = firestore_data.get('votingEndsBlock')
        self.executionStartsBlock = firestore_data.get('executionStartsBlock')
        self.executionEndsBlock = firestore_data.get('executionEndsBlock')
        self.inFavor = firestore_data.get('inFavor', "0")
        self.executionHash = firestore_data.get('executionHash', "")
        self.against = firestore_data.get('against', "0")
        self.votesFor = firestore_data.get('votesFor', 0)
        self.votesAgainst = firestore_data.get('votesAgainst', 0)
        self.externalResource = firestore_data.get(
            'externalResource', "(no link provided)")

        # Deserialize transactions if present
        transactions_data = firestore_data.get('transactions', [])
        # Assuming Txaction has a `fromJson` method
        self.transactions = [Txaction.fromJson(tx) for tx in transactions_data]

        # Deserialize votes if present
        votes_data = firestore_data.get('votes', [])
        # Assuming Vote has a `fromJson` method
        self.votes = [Vote.fromJson(vote) for vote in votes_data]


class Vote:
    def __init__(self, votingPower: str, voter: str, proposalID: str, option: int, castAt=None):
        self.voter: str = voter
        self.hash = ""
        self.proposalID: str = proposalID
        self.option: int = option
        self.reason: Optional[str] = None
        self.votingPower: str = votingPower
        self.castAt: datetime = castAt if castAt else datetime.now(
            timezone.utc)

    def toJson(self):
        return {
            'weight': self.votingPower,
            'cast': self.castAt.isoformat(),
            'voter': self.voter,
            'reason': self.reason,
            'option': self.option,
            'hash': self.hash
        }

```

### `apps/homebase/eventSignatures.py`
```py
quorum_function_abi = {
    "name": "quorum",
    "inputs": [
        {"name": "newQuorumNumerator", "type": "uint256"},
    ],
}


voting_period_function_abi = {
    "name": "setVotingPeriod",
    "inputs": [
        {"name": "newVotingPeriod", "type": "uint32"},
    ],
}

proposal_threshold_function_abi = {
    "name": "setProposalThreshold",
    "inputs": [
        {"name": "newProposalThreshold", "type": "uint256"},
    ],
}
voting_delay_function_abi = {
    "name": "setVotingDelay",
    "inputs": [
        {"name": "newVotingDelay", "type": "uint48"},
    ],
}



```

### `apps/homebase/paper.py`
```py
from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal # Add wrapper_w_abi if different
from datetime import datetime, timezone, timedelta # timedelta might be useful
from apps.homebase.entities import ProposalStatus, Proposal, StateInContract, Txaction, Token, Member, Org, Vote
import re
from web3 import Web3
from google.cloud import firestore
import codecs # Not used in current snippet, can remove if not needed elsewhere
from apps.generic.converting import decode_function_parameters # Ensure this path is correct
from apps.homebase.eventSignatures import quorum_function_abi, voting_period_function_abi,proposal_threshold_function_abi, voting_delay_function_abi


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
```

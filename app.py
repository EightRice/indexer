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
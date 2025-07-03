# indexer/app.py

from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal, wrapper_w_abi
from apps.homebase.paper import Paper
from datetime import datetime, timezone
import time
from firebase_admin import initialize_app, firestore, credentials
from web3 import Web3
import os
import sys
import re
import argparse # <<< ADDED: For handling command-line arguments

# --- Argument Parsing to select network ---
parser = argparse.ArgumentParser(description="Homebase Indexer for a specific Etherlink network.")
parser.add_argument(
    'network', 
    choices=['mainnet', 'testnet'], 
    help="The network to run the indexer on ('mainnet' or 'testnet')."
)
args = parser.parse_args()
# --- End of Argument Parsing ---


# --- Network-specific Configuration ---
if args.network == 'mainnet':
    firestore_doc_name = "Etherlink"
    rpc = "https://node.mainnet.etherlink.com"
    dao_collection_name = "idaosEtherlink" # Standard mainnet collection name
    print("--- CONFIGURATION: MAINNET ---")
elif args.network == 'testnet':
    firestore_doc_name = "Etherlink-Testnet"
    rpc = "https://node.ghostnet.etherlink.com"
    dao_collection_name = "idaosEtherlink-Testnet" # Your existing testnet collection name
    print("--- CONFIGURATION: TESTNET ---")
else:
    # This case is technically handled by argparse's `choices`, but it's good practice
    print(f"FATAL: Invalid network '{args.network}' specified.")
    sys.exit(1)
# --- End of Network Configuration ---


# --- Firebase and Web3 Setup ---
cred = credentials.Certificate('homebase.json')
initialize_app(cred) # <<< MODIFIED: Give each app a unique name to avoid conflicts
db = firestore.client()
networks = db.collection("contracts")
ceva = networks.document(firestore_doc_name).get() # <<< MODIFIED: Use variable for doc name
wrapper_address = ceva.to_dict()['wrapper']
wrapper_w_address = ceva.to_dict()['wrapper_w']

print(f"RPC Endpoint: {rpc}")
print("Original Wrapper address: " + str(wrapper_address))
print("Wrapped Token Wrapper address: " + str(wrapper_w_address))

web3 = Web3(Web3.HTTPProvider(rpc))
if not web3.is_connected():
    print("FATAL: Node connection failed!")
    sys.exit()
print("Node connected successfully.")


# --- Event Signature Generation ---
event_signatures = {
    web3.keccak(text="NewDaoCreated(address,address,address[],uint256[],string,string,string,uint256,address,string[],string[])").hex(): "NewDaoCreated",
    web3.keccak(text="DaoWrappedDeploymentInfo(address,address,address,string,string,string,uint8)").hex(): "DaoWrappedDeploymentInfo",
    web3.keccak(text="DelegateChanged(address,address,address)").hex(): "DelegateChanged",
    web3.keccak(text="ProposalCreated(uint256,address,address[],uint256[],string[],bytes[],uint256,uint256,string)").hex(): "ProposalCreated",
    web3.keccak(text="ProposalQueued(uint256,uint256)").hex(): "ProposalQueued",
    web3.keccak(text="ProposalExecuted(uint256)").hex(): "ProposalExecuted",
    web3.keccak(text="VoteCast(address,uint256,uint8,uint256,string)").hex(): "VoteCast"
}

print("--- Initializing with Monitored Event Signatures ---")
for hash_val, name in event_signatures.items():
    print(f"- {name}: {hash_val}")
print("----------------------------------------------------")


# --- Initial DAO and Paper Object Hydration ---
papers = {}
daos_collection = db.collection(dao_collection_name) # <<< MODIFIED: Use variable for collection name
docs = list(daos_collection.stream())
dao_addresses = [doc.id for doc in docs]

for doc in docs:
    obj = doc.to_dict()
    try:
        token_address = obj.get('token')
        dao_address = obj.get('address')
        if not token_address or not dao_address:
            print(f"Skipping DAO hydration for doc {doc.id} due to missing address or token field.")
            continue
        
        p = Paper(address=token_address, kind="token", 
                  daos_collection=daos_collection, db=db, web3=web3, dao=dao_address)
        dao = Paper(address=dao_address, kind="dao", token=p, 
                    daos_collection=daos_collection, db=db, web3=web3, dao=dao_address)
        papers.update({token_address: p})
        papers.update({dao_address: dao})
    except Exception as e:
        print(f"A DAO contract ({doc.id}) could not be parsed correctly: {e}")

papers.update({wrapper_address: Paper(address=wrapper_address, 
              kind="wrapper", daos_collection=daos_collection, db=db, web3=web3)})
papers.update({wrapper_w_address: Paper(address=wrapper_w_address,
              kind="wrapper_w", daos_collection=daos_collection, db=db, web3=web3)})


# --- Build list of addresses to listen to ---
listening_to_addresses = [wrapper_address, wrapper_w_address]
listening_to_addresses.extend(dao_addresses)
known_token_addresses = [paper.address for addr, paper in papers.items() if paper.kind == "token" and paper.address]
listening_to_addresses.extend(known_token_addresses)
listening_to_addresses = list(set([addr for addr in listening_to_addresses if addr]))

print(f"\nListening for {len(event_signatures)} events on {len(listening_to_addresses)} contracts.")


# --- Main Indexing Loop ---
processed_transactions = set()
heartbeat = 0
while True:
    heartbeat += 1
    try:
        latest = web3.eth.block_number
        first = latest - 15 if latest > 15 else 0 
        
        logs = web3.eth.get_logs({
            "fromBlock": first,
            "toBlock": latest,
            "address": listening_to_addresses,
        })

        if logs:
            print(f"[{args.network.upper()}] Found {len(logs)} logs between blocks {first} and {latest}") # <<< MODIFIED: Added network context to log

        for log_entry in logs:
            tx_hash = log_entry["transactionHash"].hex()
            if tx_hash in processed_transactions:
                continue 
            
            processed_transactions.add(tx_hash)
            contract_address = Web3.to_checksum_address(log_entry["address"])
            
            if not log_entry["topics"]:
                print(f"Skipping log with no topics: {log_entry}")
                continue
            
            event_signature_from_log = log_entry["topics"][0].hex()
            event_name = event_signatures.get(event_signature_from_log)

            if not event_name:
                # This log is verbose, you can comment it out if it's too noisy
                # print(f"Skipping unknown event with signature {event_signature_from_log} from contract {contract_address}")
                continue

            print(f"-> [{args.network.upper()}] Event: {event_name}, Contract: {contract_address}, Tx: {tx_hash}") # <<< MODIFIED: Added network context
            
            if contract_address not in papers:
                print(f"Paper object not found for contract address: {contract_address}. Skipping event.")
                continue

            new_contract_addresses = papers[contract_address].handle_event(log_entry, func=event_name)
            
            if new_contract_addresses:
                dao_address_new, token_address_new = new_contract_addresses
                if dao_address_new and token_address_new:
                    print(f"Adding new DAO {dao_address_new} and Token {token_address_new} to listener.")
                    if dao_address_new not in listening_to_addresses: listening_to_addresses.append(dao_address_new)
                    if token_address_new not in listening_to_addresses: listening_to_addresses.append(token_address_new)
                    
                    if token_address_new not in papers:
                        p_new_token = Paper(address=token_address_new, kind="token", daos_collection=daos_collection, db=db, dao=dao_address_new, web3=web3)
                        papers.update({token_address_new: p_new_token})
                    else:
                        p_new_token = papers[token_address_new]
                    
                    if dao_address_new not in papers:
                        papers.update({dao_address_new: Paper(token=p_new_token, address=dao_address_new, kind="dao", daos_collection=daos_collection, db=db, dao=dao_address_new, web3=web3)})
                    print(f"Now listening to {len(listening_to_addresses)} addresses.")

    except Exception as e:
        import traceback
        print(f"MAIN LOOP ERROR [{args.network.upper()}]: {e}")
        print(traceback.format_exc())
        try:
            web3 = Web3(Web3.HTTPProvider(rpc))
            if web3.is_connected(): print("Node reconnected successfully.")
            else: print("Node reconnection failed!")
        except Exception as recon_e:
            print(f"Error during reconnection: {recon_e}")
            
    if heartbeat % 50 == 0:
        print(f"[{args.network.upper()}] Heartbeat: {heartbeat}. Listening to {len(listening_to_addresses)} addresses.")

    time.sleep(5)
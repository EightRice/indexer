# indexer/app.py

# --- Imports ---
from apps.homebase.paper import Paper as HomebasePaper
from apps.afterme.paper import Paper as AftermePaper
from datetime import datetime, timezone
import time
from firebase_admin import initialize_app, firestore, credentials
from web3 import Web3
import os
import sys
import re
import argparse

# --- Argument Parsing to select network and app ---
parser = argparse.ArgumentParser(description="Unified Indexer for Homebase and AfterMe on Etherlink.")
parser.add_argument(
    'network', 
    choices=['mainnet', 'testnet'], 
    help="The network to run the indexer on ('mainnet' or 'testnet')."
)
# NEW: Optional argument for selecting the app. Defaults to 'all'.
parser.add_argument(
    'app',
    nargs='?',
    default='all',
    choices=['homebase', 'afterme', 'all'],
    help="The specific app to index ('homebase', 'afterme'). If omitted, runs 'all'."
)
args = parser.parse_args()
# --- End of Argument Parsing ---

# NEW: Flags to control which parts of the script run
run_homebase = args.app in ['homebase', 'all']
run_afterme = args.app in ['afterme', 'all']

print(f"--- Indexer starting for Network: {args.network.upper()}, App(s): {args.app.upper()} ---")

# --- Network-specific Configuration ---
if args.network == 'mainnet':
    homebase_fs_doc_name = "Etherlink"
    afterme_fs_doc_name = "Etherlink"
    rpc = "https://node.mainnet.etherlink.com"
    homebase_dao_collection_name = "idaosEtherlink"
    afterme_wills_collection_name = "willsEtherlink"
elif args.network == 'testnet':
    homebase_fs_doc_name = "Etherlink-Testnet"
    afterme_fs_doc_name = "Etherlink-Testnet"
    rpc = "https://node.ghostnet.etherlink.com"
    homebase_dao_collection_name = "idaosEtherlink-Testnet"
    afterme_wills_collection_name = "willsEtherlink-Testnet"
else:
    print(f"FATAL: Invalid network '{args.network}' specified.")
    sys.exit(1)

# --- Firebase and Web3 Setup ---
if run_homebase:
    try:
        cred_homebase = credentials.Certificate('homebase.json')
        homebase_app = initialize_app(cred_homebase, name='homebaseApp')
        db_homebase = firestore.client(app=homebase_app)
        print("Firebase app 'homebaseApp' initialized.")
    except Exception as e:
        print(f"FATAL: Could not initialize Firebase for Homebase: {e}")
        sys.exit(1)

if run_afterme:
    try:
        cred_afterme = credentials.Certificate('afterme.json')
        # Use a unique name to avoid conflict if 'all' apps are running
        app_name = 'aftermeApp' if run_homebase else 'default' 
        afterme_app = initialize_app(cred_afterme, name=app_name)
        db_afterme = firestore.client(app=afterme_app)
        print(f"Firebase app '{app_name}' for AfterMe initialized.")
    except Exception as e:
        print(f"FATAL: Could not initialize Firebase for AfterMe: {e}")
        sys.exit(1)

web3 = Web3(Web3.HTTPProvider(rpc))
if not web3.is_connected():
    print("FATAL: Node connection failed!")
    sys.exit()
print(f"Node connected successfully to {rpc}")

# --- Contract Addresses and Event Signatures Setup ---
listening_to_addresses = []
event_signatures = {}
papers = {}

if run_homebase:
    homebase_networks = db_homebase.collection("contracts")
    homebase_doc = homebase_networks.document(homebase_fs_doc_name).get()
    if homebase_doc.exists:
        homebase_config = homebase_doc.to_dict()
        homebase_wrapper_address = homebase_config['wrapper']
        homebase_wrapper_w_address = homebase_config['wrapper_w']
        print(f"Homebase Wrapper address: {homebase_wrapper_address}")
        listening_to_addresses.extend([homebase_wrapper_address, homebase_wrapper_w_address])

        event_signatures.update({
            web3.keccak(text="NewDaoCreated(address,address,address[],uint256[],string,string,string,uint256,address,string[],string[])").hex(): "NewDaoCreated",
            web3.keccak(text="DaoWrappedDeploymentInfo(address,address,address,string,string,string,uint8)").hex(): "DaoWrappedDeploymentInfo",
            web3.keccak(text="DelegateChanged(address,address,address)").hex(): "DelegateChanged",
            web3.keccak(text="ProposalCreated(uint256,address,address[],uint256[],string[],bytes[],uint256,uint256,string)").hex(): "ProposalCreated",
            web3.keccak(text="ProposalQueued(uint256,uint256)").hex(): "ProposalQueued",
            web3.keccak(text="ProposalExecuted(uint256)").hex(): "ProposalExecuted",
            web3.keccak(text="VoteCast(address,uint256,uint8,uint256,string)").hex(): "VoteCast"
        })

        daos_collection = db_homebase.collection(homebase_dao_collection_name)
        homebase_docs = list(daos_collection.stream())
        dao_addresses = [doc.id for doc in homebase_docs]
        listening_to_addresses.extend(dao_addresses)

        for doc in homebase_docs:
            obj = doc.to_dict()
            token_address, dao_address = obj.get('token'), obj.get('address')
            if token_address and dao_address:
                listening_to_addresses.append(token_address)
                p = HomebasePaper(address=token_address, kind="token", daos_collection=daos_collection, db=db_homebase, web3=web3, dao=dao_address)
                dao = HomebasePaper(address=dao_address, kind="dao", token=p, daos_collection=daos_collection, db=db_homebase, web3=web3, dao=dao_address)
                papers.update({token_address: p, dao_address: dao})

        papers.update({homebase_wrapper_address: HomebasePaper(address=homebase_wrapper_address, kind="wrapper", daos_collection=daos_collection, db=db_homebase, web3=web3)})
        papers.update({homebase_wrapper_w_address: HomebasePaper(address=homebase_wrapper_w_address, kind="wrapper_w", daos_collection=daos_collection, db=db_homebase, web3=web3)})
    else:
        print(f"WARNING: Homebase config document '{homebase_fs_doc_name}' not found. Skipping Homebase setup.")
        run_homebase = False # Disable if config is missing

if run_afterme:
    afterme_networks = db_afterme.collection("networks")
    afterme_doc = afterme_networks.document(afterme_fs_doc_name).get()
    if afterme_doc.exists:
        afterme_config = afterme_doc.to_dict()
        afterme_source_address = afterme_config['sourceContractAddress']
        print(f"AfterMe Source address: {afterme_source_address}")
        listening_to_addresses.append(afterme_source_address)

        event_signatures.update({
            web3.keccak(text="WillCreated(address,address)").hex(): "WillCreated",
            web3.keccak(text="Ping(uint256)").hex(): "Ping",
            web3.keccak(text="Executed(address,uint256,address)").hex(): "Executed",
            web3.keccak(text="Cancelled(uint256)").hex(): "Cancelled",
        })

        wills_collection = db_afterme.collection(afterme_wills_collection_name)
        afterme_docs = list(wills_collection.stream())
        will_addresses = [doc.id for doc in afterme_docs]
        listening_to_addresses.extend(will_addresses)

        papers.update({afterme_source_address: AftermePaper(address=afterme_source_address, kind="afterme_source", db=db_afterme, web3=web3, wills_collection_name=afterme_wills_collection_name)})
        for will_address in will_addresses:
            papers.update({will_address: AftermePaper(address=will_address, kind="afterme_will", db=db_afterme, web3=web3, wills_collection_name=afterme_wills_collection_name)})
    else:
        print(f"WARNING: AfterMe config document '{afterme_fs_doc_name}' not found. Skipping AfterMe setup.")
        run_afterme = False # Disable if config is missing

# Finalize listener setup
listening_to_addresses = list(set([addr for addr in listening_to_addresses if addr]))
print("\n--- Initializing with Monitored Event Signatures ---")
for hash_val, name in event_signatures.items():
    print(f"- {name}: {hash_val}")
print("----------------------------------------------------")
print(f"Listening for {len(event_signatures)} events on {len(listening_to_addresses)} contracts.")

# --- Main Indexing Loop ---
processed_transactions = set()
heartbeat = 0
while True:
    heartbeat += 1
    try:
        if not listening_to_addresses:
            print("No contracts to listen to. Waiting...")
            time.sleep(15)
            continue

        latest = web3.eth.block_number
        first = latest - 15 if latest > 15 else 0 
        
        logs = web3.eth.get_logs({"fromBlock": first, "toBlock": latest, "address": listening_to_addresses})
        if logs:
            print(f"[{args.network.upper()}] Found {len(logs)} logs between blocks {first} and {latest}")

        for log_entry in logs:
            tx_hash = log_entry["transactionHash"].hex()
            if tx_hash in processed_transactions: continue 
            
            processed_transactions.add(tx_hash)
            contract_address = Web3.to_checksum_address(log_entry["address"])
            
            if not log_entry["topics"]: continue
            
            event_signature_from_log = log_entry["topics"][0].hex()
            event_name = event_signatures.get(event_signature_from_log)

            if not event_name: continue
            print(f"-> [{args.network.upper()}] Event: {event_name}, Contract: {contract_address}, Tx: {tx_hash}")
            
            if contract_address not in papers:
                print(f"Paper object not found for {contract_address}. Skipping event.")
                continue

            new_contract_info = papers[contract_address].handle_event(log_entry, func=event_name)
            
            if run_homebase and isinstance(new_contract_info, list) and len(new_contract_info) == 2:
                dao_address_new, token_address_new = new_contract_info
                print(f"Adding new Homebase DAO {dao_address_new} and Token {token_address_new} to listener.")
                if dao_address_new not in listening_to_addresses: listening_to_addresses.append(dao_address_new)
                if token_address_new not in listening_to_addresses: listening_to_addresses.append(token_address_new)
                
                if token_address_new not in papers:
                    p_new_token = HomebasePaper(address=token_address_new, kind="token", daos_collection=daos_collection, db=db_homebase, dao=dao_address_new, web3=web3)
                    papers.update({token_address_new: p_new_token})
                else: p_new_token = papers[token_address_new]
                
                if dao_address_new not in papers:
                    papers.update({dao_address_new: HomebasePaper(token=p_new_token, address=dao_address_new, kind="dao", daos_collection=daos_collection, db=db_homebase, dao=dao_address_new, web3=web3)})
                print(f"Now listening to {len(listening_to_addresses)} addresses.")
            
            elif run_afterme and isinstance(new_contract_info, str) and new_contract_info.startswith("0x"):
                new_will_address = new_contract_info
                print(f"Adding new AfterMe Will {new_will_address} to listener.")
                if new_will_address not in listening_to_addresses: listening_to_addresses.append(new_will_address)
                if new_will_address not in papers:
                    papers.update({new_will_address: AftermePaper(address=new_will_address, kind="afterme_will", db=db_afterme, web3=web3, wills_collection_name=afterme_wills_collection_name)})
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
        print(f"[{args.network.upper()}] Heartbeat: {heartbeat}. Listening to {len(listening_to_addresses)} addresses on app(s): {args.app}.")

    time.sleep(5)
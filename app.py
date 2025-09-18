# indexer/app.py

# --- Imports (Standard Libraries First) ---
import sys
import os
import re
import time
import argparse
import traceback
from datetime import datetime, timezone

# --- Load .env file BEFORE any other imports that might need it ---
from dotenv import load_dotenv
load_dotenv()

# --- Imports (Heavy/Custom Libraries After .env is loaded) ---
from apps.homebase.paper import Paper as HomebasePaper
from apps.afterme.paper import Paper as AftermePaper
from apps.afterme.abis import source_abi
from firebase_admin import initialize_app, firestore, credentials
from web3 import Web3
from web3.exceptions import Web3RPCError

# --- Safe Import for Discord Alerter ---
try:
    from apps.generic.services import send_indexer_alert
    CAN_SEND_ALERTS = True
except ImportError:
    print("WARNING: 'generic/services.py' not found. Discord alerts will be disabled.")
    def send_indexer_alert(msg, network="N/A", app="N/A"): pass
    CAN_SEND_ALERTS = False

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="Unified Indexer for Homebase and AfterMe on Etherlink.")
parser.add_argument('network', choices=['mainnet', 'testnet'], help="The network to run.")
parser.add_argument('app', nargs='?', default='all', choices=['homebase', 'afterme', 'all'], help="The app to index.")
args = parser.parse_args()

def alert(message: str):
    """Helper function to safely send alerts with network/app context."""
    if CAN_SEND_ALERTS:
        send_indexer_alert(message, network=args.network, app=args.app)

# --- Control Flags ---
run_homebase = args.app in ['homebase', 'all']
run_afterme = args.app in ['afterme', 'all']

print(f"--- Indexer starting for Network: {args.network.upper()}, App(s): {args.app.upper()} ---")

# --- Network Config ---
if args.network == 'mainnet':
    homebase_fs_doc_name = "Etherlink"
    afterme_fs_doc_name = "Etherlink"
    default_rpc = "https://node.mainnet.etherlink.com"
    homebase_dao_collection_name = "idaosEtherlink"
    afterme_wills_collection_name = "willsEtherlink"
elif args.network == 'testnet':
    homebase_fs_doc_name = "Etherlink-Testnet"
    afterme_fs_doc_name = "Etherlink-Testnet"
    default_rpc = "https://node.ghostnet.etherlink.com"
    homebase_dao_collection_name = "idaosEtherlink-Testnet"
    afterme_wills_collection_name = "willsEtherlink-Testnet"
else:
    error_msg = f"Invalid network '{args.network}' specified."
    print(f"FATAL: {error_msg}")
    alert(error_msg)
    sys.exit(1)

rpc = default_rpc

# --- Firebase and Web3 Setup ---
db_homebase = None
if run_homebase:
    try:
        cred_homebase = credentials.Certificate('homebase.json')
        homebase_app = initialize_app(cred_homebase, name='homebaseApp')
        db_homebase = firestore.client(app=homebase_app)
        print("Firebase app 'homebaseApp' initialized.")
    except Exception as e:
        error_msg = f"Could not initialize Firebase for Homebase: {e}"
        print(f"FATAL: {error_msg}")
        alert(error_msg)
        sys.exit(1)

db_afterme = None
if run_afterme:
    try:
        cred_afterme = credentials.Certificate('afterme.json')
        app_name = 'aftermeApp' if run_homebase else 'default' 
        afterme_app = initialize_app(cred_afterme, name=app_name)
        db_afterme = firestore.client(app=afterme_app)
        print(f"Firebase app '{app_name}' for AfterMe initialized.")
    except Exception as e:
        error_msg = f"Could not initialize Firebase for AfterMe: {e}"
        print(f"FATAL: {error_msg}")
        traceback.print_exc()
        alert(error_msg)
        sys.exit(1)

# --- AfterMe Setup (to fetch RPC) ---
if run_afterme:
    afterme_networks = db_afterme.collection("networks")
    afterme_doc_ref = afterme_networks.document(afterme_fs_doc_name)
    afterme_doc = afterme_doc_ref.get()
    if afterme_doc.exists:
        afterme_config = afterme_doc.to_dict()
        rpc_from_fs = afterme_config.get('rpc')
        if rpc_from_fs:
            print(f"Using RPC from Firestore: {rpc_from_fs}")
            rpc = rpc_from_fs
        else:
            print(f"WARNING: RPC not found in Firestore, using default: {rpc}")
    else:
        print(f"WARNING: AfterMe config document '{afterme_fs_doc_name}' not found.")

web3 = Web3(Web3.HTTPProvider(rpc))
if not web3.is_connected():
    error_msg = f"Node connection to {rpc} failed!"
    print(f"FATAL: {error_msg}")
    alert(error_msg)
    sys.exit(1)
print(f"Node connected successfully to {rpc}")

# --- Contract Addresses and Event Signatures Setup ---
listening_to_addresses = []
event_signatures = {}
papers = {}

# --- Homebase Setup ---
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
        run_homebase = False

# --- AfterMe Setup Continued ---
if run_afterme and afterme_doc.exists:
    afterme_source_address = afterme_config['sourceContractAddress']
    print(f"AfterMe Source address: {afterme_source_address}")
    listening_to_addresses.append(afterme_source_address)
    event_signatures.update({
        web3.keccak(text="WillCreated(address,address,bool)").hex(): "WillCreated",
        web3.keccak(text="WillCleared(address,address)").hex(): "WillCleared",
        web3.keccak(text="Ping(uint256)").hex(): "Ping",
        web3.keccak(text="Executed(address,uint256,address)").hex(): "Executed",
        web3.keccak(text="Cancelled()").hex(): "Cancelled",
        web3.keccak(text="WillConfigured(address)").hex(): "WillConfigured",
        web3.keccak(text="WillEmptied(address)").hex(): "WillEmptied",
    })
    wills_collection = db_afterme.collection(afterme_wills_collection_name)
    
    print("\n--- Starting AfterMe Historical Sync ---")
    try:
        REORG_SAFETY_MARGIN = 100
        from_block_config = afterme_config.get('fromBlock', 0)
        last_synced_block = afterme_config.get('lastSyncedBlock', 0)
        start_block = max(from_block_config, last_synced_block - REORG_SAFETY_MARGIN)
        if last_synced_block > 0:
            print(f"Checkpoint found: {last_synced_block}. Applying safety margin, starting from {start_block}.")
        temp_source_contract = web3.eth.contract(address=Web3.to_checksum_address(afterme_source_address), abi=re.sub(r'\n+', ' ', source_abi).strip())
        latest_block = web3.eth.block_number
        if start_block > latest_block:
            print("Database is already up to date.")
        else:
            print(f"Scanning for AfterMe events from block {start_block} to {latest_block}...")
            created_logs, cleared_logs = [], []
            current_chunk_size, from_block_scan = 1000, start_block
            while from_block_scan <= latest_block:
                to_block = min(from_block_scan + current_chunk_size - 1, latest_block)
                try:
                    print(f"  - Scanning chunk: {from_block_scan} to {to_block} (size: {current_chunk_size})")
                    created_logs.extend(temp_source_contract.events.WillCreated.get_logs(from_block=from_block_scan, to_block=to_block))
                    cleared_logs.extend(temp_source_contract.events.WillCleared.get_logs(from_block=from_block_scan, to_block=to_block))
                    from_block_scan = to_block + 1
                    time.sleep(0.2)
                except Web3RPCError as e:
                    error_message = str(e).lower()
                    is_range_error = any(phrase in error_message for phrase in ["block range is too large", "cannot request logs over more than", "block limit"])
                    if not is_range_error:
                        try: is_range_error = e.args[0]['code'] in [-32062, -32603]
                        except (TypeError, KeyError, IndexError): pass
                    if is_range_error:
                        print(f"    - Block range limit hit. Reducing chunk size from {current_chunk_size} and retrying.")
                        current_chunk_size //= 2
                        if current_chunk_size < 1: raise Exception("Chunk size fell to zero.")
                    else: raise e
            wills_in_db_docs = list(wills_collection.stream())
            wills_in_db = {doc.id for doc in wills_in_db_docs}
            print(f"Found {len(wills_in_db)} wills in Firestore before sync.")
            created_in_range = {Web3.to_checksum_address(log['args']['willAddress']) for log in created_logs}
            cleared_in_range = {Web3.to_checksum_address(log['args']['willAddress']) for log in cleared_logs}
            master_will_list = (wills_in_db.union(created_in_range)) - cleared_in_range
            print(f"Reconciling state for {len(master_will_list)} active wills...")
            temp_paper_for_sync = AftermePaper(address="", kind="", db=db_afterme, web3=web3, wills_collection_name=afterme_wills_collection_name)
            for will_addr in master_will_list:
                print(f"Syncing will: {will_addr}")
                try:
                    will_data = temp_paper_for_sync.get_onchain_will_data(will_addr)
                    if will_data: wills_collection.document(will_addr).set(will_data, merge=True)
                except Exception as e:
                    print(f"  - ERROR syncing will {will_addr}: {e}")
            wills_to_delete = wills_in_db - master_will_list
            if wills_to_delete:
                print(f"Found {len(wills_to_delete)} wills to delete from Firestore.")
                for will_addr in wills_to_delete:
                    print(f"Deleting cleared will: {will_addr}")
                    wills_collection.document(will_addr).delete()
        afterme_doc_ref.update({'lastSyncedBlock': latest_block})
        print(f"--- AfterMe Historical Sync Complete --- Checkpoint updated to block {latest_block}.")
    except Exception as e:
        error_msg = f"Could not complete AfterMe historical sync: {e}"
        print(f"FATAL: {error_msg}")
        traceback.print_exc()
        alert(error_msg)
        print("WARNING: Continuing without full historical sync.")
    
    afterme_docs = list(wills_collection.stream())
    will_addresses = [doc.id for doc in afterme_docs]
    listening_to_addresses.extend(will_addresses)
    papers.update({afterme_source_address: AftermePaper(address=afterme_source_address, kind="afterme_source", db=db_afterme, web3=web3, wills_collection_name=afterme_wills_collection_name)})
    for will_address in will_addresses:
        papers.update({will_address: AftermePaper(address=will_address, kind="afterme_will", db=db_afterme, web3=web3, wills_collection_name=afterme_wills_collection_name)})
elif run_afterme:
    print(f"WARNING: AfterMe config document '{afterme_fs_doc_name}' not found. Skipping AfterMe setup.")
    run_afterme = False

# --- Finalize Listener Setup ---
listening_to_addresses = list(set([addr for addr in listening_to_addresses if addr]))
print("\n--- Initializing with Monitored Event Signatures ---")
for hash_val, name in event_signatures.items():
    print(f"- {name}: {hash_val}")
print("----------------------------------------------------")
print(f"Listening for {len(event_signatures)} events on {len(listening_to_addresses)} contracts.")

# --- Main Indexing Loop ---
heartbeat = 0
# --- START OF CHANGE: Stateful Indexing Logic ---
last_processed_block = 0
BLOCK_HEADROOM = 3  # Query logs 3 blocks behind the head of the chain for safety
MAX_BLOCK_RANGE = 100 # Process a maximum of 100 blocks at a time to not overload the RPC

while True:
    heartbeat += 1
    try:
        if not listening_to_addresses:
            print("No contracts to listen to. Waiting...")
            time.sleep(15)
            continue

        latest_on_chain = web3.eth.block_number

        # Initialize our block tracker on the first run.
        if last_processed_block == 0:
            last_processed_block = latest_on_chain - 15 # Start with the original 15-block window for the first time
            print(f"Initializing indexer. Starting from block {last_processed_block}.")
            continue

        # Determine the range of blocks to scan in this iteration
        from_block = last_processed_block + 1
        to_block = latest_on_chain - BLOCK_HEADROOM

        # If we are already caught up, just wait.
        if from_block > to_block:
            time.sleep(5)
            continue

        # If the indexer has been offline, process in manageable chunks.
        if (to_block - from_block) > MAX_BLOCK_RANGE:
            to_block = from_block + MAX_BLOCK_RANGE - 1
            print(f"[{args.network.upper()}] Large gap detected. Processing chunk: {from_block} -> {to_block}")

        logs = web3.eth.get_logs({"fromBlock": from_block, "toBlock": to_block, "address": listening_to_addresses})
        
        if logs:
            print(f"[{args.network.upper()}] Found {len(logs)} logs between blocks {from_block} and {to_block}")

        for log_entry in logs:
            tx_hash = log_entry["transactionHash"].hex()
            # The processed_transactions check was removed from here as it was causing events within the same tx to be skipped.
            # The block-by-block processing already prevents reprocessing old transactions.
            contract_address = Web3.to_checksum_address(log_entry["address"])
            if not log_entry["topics"]: continue
            event_signature_from_log = log_entry["topics"][0].hex()
            event_name = event_signatures.get(event_signature_from_log)
            if not event_name: continue
            print(f"-> Event: {event_name}, Contract: {contract_address}, Tx: {tx_hash}")
            if contract_address not in papers:
                print(f"WARNING: Paper object not found for {contract_address}. Skipping event.")
                continue
            new_contract_info = papers[contract_address].handle_event(log_entry, func=event_name)
            
            # This logic remains the same
            if new_contract_info == "DELETED":
                print(f"Removing cleared will {contract_address} from active listeners.")
                if contract_address in listening_to_addresses: listening_to_addresses.remove(contract_address)
                if contract_address in papers: del papers[contract_address]
                print(f"Now listening to {len(listening_to_addresses)} addresses.")
            elif run_homebase and isinstance(new_contract_info, list) and len(new_contract_info) == 2:
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

        # IMPORTANT: Update our checkpoint after a successful run
        last_processed_block = to_block
        
    except Web3RPCError as e:
        # This is a more robust way to catch recoverable errors without crashing
        error_message = str(e).lower()
        is_recoverable_error = any(phrase in error_message for phrase in ["block range", "unavailable", "block limit", "server error"])
        if is_recoverable_error:
            print(f"WARN: Recoverable RPC error encountered: {e}. Waiting 10s before retry.")
            time.sleep(10) # Wait a bit longer for the node to sort itself out
        else:
            # For other, more severe RPC errors, trigger the full reconnect
            raise e
    except Exception as e:
        error_msg = f"MAIN LOOP ERROR: {e}"
        print(error_msg)
        traceback.print_exc()
        alert(error_msg)
        try:
            web3 = Web3(Web3.HTTPProvider(rpc))
            if web3.is_connected(): print("Node reconnected successfully.")
            else: print("Node reconnection failed!")
        except Exception as recon_e:
            print(f"Error during reconnection: {recon_e}")
            
    if heartbeat % 50 == 0:
        print(f"[{args.network.upper()}] Heartbeat: {heartbeat}. Listening to {len(listening_to_addresses)} addresses on app(s): {args.app}. Current Block: {last_processed_block}")
    
    # Adjust sleep time based on whether we are catching up or fully synced
    if (latest_on_chain - last_processed_block) > MAX_BLOCK_RANGE:
        time.sleep(0.5) # Sleep less if we are catching up
    else:
        time.sleep(5)   # Normal 5-second sleep if we are near the head
# indexer/app.py
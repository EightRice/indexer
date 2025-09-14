# Project: indexer

## Folder Structure

- indexer/
  - app.py
  - apps/
    - afterme/
      - abis.py
      - entities.py
      - paper.py
      - paper.py.errors.txt
    - homebase/
      - abis.py
      - entities.py
      - eventSignatures.py
      - paper.py

## File Contents

### `app.py`
```py
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
    from generic.services import send_indexer_alert
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
            print(f"-> Event: {event_name}, Contract: {contract_address}, Tx: {tx_hash}")
            if contract_address not in papers:
                print(f"WARNING: Paper object not found for {contract_address}. Skipping event.")
                continue
            new_contract_info = papers[contract_address].handle_event(log_entry, func=event_name)
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
        print(f"[{args.network.upper()}] Heartbeat: {heartbeat}. Listening to {len(listening_to_addresses)} addresses on app(s): {args.app}.")
    time.sleep(5)
# indexer/app.py
```

### `apps/afterme/abis.py`
```py
# indexer/apps/afterme/abis.py

# ABI for the Source contract that creates Wills
source_abi = '''
[
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "user",
        "type": "address"
      },
      {
        "indexed": true,
        "internalType": "address",
        "name": "willAddress",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "bool",
        "name": "hasDiary",
        "type": "bool"
      }
    ],
    "name": "WillCreated",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "user",
        "type": "address"
      },
      {
        "indexed": true,
        "internalType": "address",
        "name": "willAddress",
        "type": "address"
      }
    ],
    "name": "WillCleared",
    "type": "event"
  }
]
'''

# ABI for the individual Will contracts
will_abi = '''
[
  {
    "anonymous": false,
    "inputs": [],
    "name": "Cancelled",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": false,
        "internalType": "address",
        "name": "executor",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "uint256",
        "name": "ethFee",
        "type": "uint256"
      },
      {
        "indexed": false,
        "internalType": "address",
        "name": "feeRecipient",
        "type": "address"
      }
    ],
    "name": "Executed",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": false,
        "internalType": "uint256",
        "name": "newLastUpdate",
        "type": "uint256"
      }
    ],
    "name": "Ping",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "owner",
        "type": "address"
      }
    ],
    "name": "WillConfigured",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "owner",
        "type": "address"
      }
    ],
    "name": "WillEmptied",
    "type": "event"
  },
  {
    "inputs": [],
    "name": "getWillDetails",
    "outputs": [
      {
        "components": [
          { "internalType": "address", "name": "owner", "type": "address" },
          { "internalType": "uint256", "name": "interval", "type": "uint256" },
          { "internalType": "uint256", "name": "lastUpdate", "type": "uint256" },
          { "internalType": "bool", "name": "executed", "type": "bool" },
          { "internalType": "bool", "name": "hasDiary", "type": "bool" },
          { "internalType": "uint256", "name": "ethBalance", "type": "uint256" },
          { "internalType": "address[]", "name": "heirs", "type": "address[]" },
          { "internalType": "uint256[]", "name": "distributionPercentages", "type": "uint256[]" },
          {
            "components": [
              { "internalType": "address", "name": "tokenContract", "type": "address" },
              { "internalType": "uint256", "name": "balance", "type": "uint256" }
            ],
            "internalType": "struct Erc20Detail[]",
            "name": "erc20Details",
            "type": "tuple[]"
          },
          {
            "components": [
              { "internalType": "address", "name": "tokenContract", "type": "address" },
              { "internalType": "uint256", "name": "tokenId", "type": "uint256" },
              { "internalType": "address", "name": "heir", "type": "address" }
            ],
            "internalType": "struct Erc721Detail[]",
            "name": "erc721Details",
            "type": "tuple[]"
          }
        ],
        "internalType": "struct WillDetails",
        "name": "",
        "type": "tuple"
      }
    ],
    "stateMutability": "view",
    "type": "function"
  }
]
'''
# indexer/apps/afterme/abis.py
```

### `apps/afterme/entities.py`
```py
# indexer/apps/afterme/entities.py
from datetime import datetime, timezone

class Will:
    def __init__(self, address, owner, interval, last_update_timestamp, executed, has_diary, status):
        self.address = address
        self.owner = owner
        self.interval = interval
        self.last_update = datetime.fromtimestamp(last_update_timestamp, tz=timezone.utc)
        self.executed = executed
        self.hasDiary = has_diary
        self.status = status # 'Empty', 'Active', 'Executed'
        self.created_at = datetime.now(timezone.utc)

    def to_firestore(self):
        """Serializes the object to a dictionary for Firestore."""
        return {
            'address': self.address,
            'owner': self.owner,
            'interval': self.interval, # in seconds
            'lastUpdate': self.last_update, # as a datetime object
            'executed': self.executed,
            'hasDiary': self.hasDiary,
            'status': self.status,
            'createdAt': self.created_at,
            'lastIndexed': datetime.now(timezone.utc)
        }
# indexer/apps/afterme/entities.py
```

### `apps/afterme/paper.py`
```py
# indexer/apps/afterme/paper.py

import re
from web3 import Web3
from apps.afterme.abis import source_abi, will_abi
from apps.afterme.entities import Will
from datetime import datetime, timezone
import traceback

class Paper:
    def __init__(self, address, kind, web3, db, wills_collection_name):
        self.address = address
        self.kind = kind
        self.web3: Web3 = web3
        self.db = db
        self.wills_collection = db.collection(wills_collection_name)
        self.abi_string = None
        self.contract = None

        if kind == "afterme_source":
            self.abi_string = source_abi
        elif kind == "afterme_will":
            self.abi_string = will_abi
        
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
                print(f"Error creating AfterMe contract object for {self.address} with kind {self.kind}: {e}")
                return None
        return self.contract

    def get_specific_contract(self, address, abi):
        """Helper to get a contract instance with a specific address and ABI."""
        try:
            final_abi = abi
            if isinstance(abi, str):
                final_abi = re.sub(r'\n+', ' ', abi).strip()
            return self.web3.eth.contract(address=Web3.to_checksum_address(address), abi=final_abi)
        except Exception as e:
            print(f"Error creating specific AfterMe contract {address}: {e}")
            return None

    def get_onchain_will_data(self, will_address):
        """Fetches will details from the chain and formats it for Firestore."""
        will_contract = self.get_specific_contract(will_address, will_abi)
        if not will_contract:
            print(f"Could not instantiate Will contract at {will_address}")
            return None

        details = will_contract.functions.getWillDetails().call()
        (owner, interval, last_update, executed, has_diary, _eth_balance, 
         _heirs, _dist_percentages, _erc20s, _nfts) = details
        
        status = 'Active'
        if executed:
            status = 'Executed'
        elif interval == 0:
            status = 'Empty'

        will_entity = Will(
            address=will_address,
            owner=Web3.to_checksum_address(owner),
            interval=interval,
            last_update_timestamp=last_update,
            executed=executed,
            has_diary=has_diary,
            status=status
        )
        return will_entity.to_firestore()

    def handle_will_created(self, log):
        """Handles the WillCreated event from the source contract."""
        contract_instance = self.get_contract()
        if not contract_instance: return None

        try:
            decoded_event = contract_instance.events.WillCreated().process_log(log)
            will_address = Web3.to_checksum_address(decoded_event['args']['willAddress'])
            print(f"New Will created: {will_address}")

            will_data = self.get_onchain_will_data(will_address)
            if not will_data: return None

            self.wills_collection.document(will_address).set(will_data)
            print(f"Successfully stored new Will {will_address} in Firestore with status '{will_data.get('status')}'.")
            
            return will_address

        except Exception as e:
            print(f"Error processing WillCreated event: {e}")
            traceback.print_exc()
        return None

    def handle_will_configured(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Configuration received for Will: {will_address}")
        try:
            will_data = self.get_onchain_will_data(will_address)
            if not will_data: return
            self.wills_collection.document(will_address).update(will_data)
            print(f"Successfully updated and set Will {will_address} to 'Active'.")
        except Exception as e:
            print(f"Error handling WillConfigured for {will_address}: {e}")

    def handle_will_emptied(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Emptied event received for Will: {will_address}")
        try:
            will_data = self.get_onchain_will_data(will_address)
            if not will_data: return
            self.wills_collection.document(will_address).update(will_data)
            print(f"Successfully updated and set Will {will_address} to 'Empty'.")
        except Exception as e:
            print(f"Error handling WillEmptied for {will_address}: {e}")

    def handle_ping(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Ping received for Will: {will_address}")
        try:
            contract_instance = self.get_contract()
            if not contract_instance: return
            decoded_event = contract_instance.events.Ping().process_log(log)
            new_last_update_ts = decoded_event['args']['newLastUpdate']
            update_data = {
                'lastUpdate': datetime.fromtimestamp(new_last_update_ts, tz=timezone.utc),
                'lastIndexed': datetime.now(timezone.utc)
            }
            self.wills_collection.document(will_address).update(update_data)
            print(f"Successfully updated lastUpdate for Will {will_address}.")
        except Exception as e:
            print(f"Error handling Ping for Will {will_address}: {e}")

    def handle_executed(self, log):
        will_address = Web3.to_checksum_address(log['address'])
        tx_hash = log['transactionHash'].hex()
        print(f"Execution reported for Will: {will_address} in Tx: {tx_hash}")
        try:
            update_data = {
                'executed': True,
                'status': 'Executed',
                'executionTime': datetime.now(timezone.utc),
                'transactionHash': tx_hash,
                'lastIndexed': datetime.now(timezone.utc)
            }
            self.wills_collection.document(will_address).update(update_data)
            print(f"Marked Will {will_address} as executed with status 'Executed'.")
        except Exception as e:
            print(f"Error marking Will {will_address} as executed: {e}")

    def handle_cancelled(self, log):
        """Handles the Cancelled event from a Will contract, deleting it and signaling removal."""
        will_address = Web3.to_checksum_address(log['address'])
        print(f"Cancellation reported for Will: {will_address}")
        try:
            self.wills_collection.document(will_address).delete()
            print(f"Successfully deleted Will {will_address} from Firestore.")
            return "DELETED"  # Signal to the main loop to remove this from listeners
        except Exception as e:
            print(f"Error deleting Will {will_address}: {e}")
        return None
            
    def handle_event(self, log, func=None):
        if self.kind == "afterme_source":
            if func == "WillCreated":
                return self.handle_will_created(log)
        elif self.kind == "afterme_will":
            if func == "Ping":
                self.handle_ping(log)
            elif func == "Executed":
                self.handle_executed(log)
            elif func == "Cancelled":
                return self.handle_cancelled(log)
            elif func == "WillConfigured":
                self.handle_will_configured(log)
            elif func == "WillEmptied":
                self.handle_will_emptied(log)
        return None
# indexer/apps/afterme/paper.py
```

### `apps/afterme/paper.py.errors.txt`
```txt
Problems found in: paper.py
==================================================

ERROR at Line 59:24 (Pylance)
   Message: Expected expression

ERROR at Line 58:9 (Pylance)
   Message: "(" was not closed

WARNING at Line 63:44 (Pylance)
   Message: "owner" is not defined

WARNING at Line 64:22 (Pylance)
   Message: "interval" is not defined

WARNING at Line 65:35 (Pylance)
   Message: "last_update" is not defined

WARNING at Line 66:22 (Pylance)
   Message: "executed" is not defined

WARNING at Line 67:23 (Pylance)
   Message: "has_diary" is not defined

WARNING at Line 57:9 (Pylance)
   Message: "details" is not accessed


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


wrapper_token_abi = '''
[
	{
		"inputs": [
			{
				"internalType": "contract IERC20",
				"name": "underlyingToken",
				"type": "address"
			},
			{
				"internalType": "string",
				"name": "name_",
				"type": "string"
			},
			{
				"internalType": "string",
				"name": "symbol_",
				"type": "string"
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
				"internalType": "address",
				"name": "token",
				"type": "address"
			}
		],
		"name": "ERC20InvalidUnderlying",
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
				"internalType": "address",
				"name": "token",
				"type": "address"
			}
		],
		"name": "SafeERC20FailedOperation",
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
		"stateMutability": "pure",
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
		"inputs": [],
		"name": "admin",
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
		"inputs": [
			{
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "depositFor",
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
				"name": "owner_",
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
		"inputs": [
			{
				"internalType": "address",
				"name": "newAdmin",
				"type": "address"
			}
		],
		"name": "setAdmin",
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
	},
	{
		"inputs": [],
		"name": "underlying",
		"outputs": [
			{
				"internalType": "contract IERC20",
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
				"internalType": "address",
				"name": "account",
				"type": "address"
			},
			{
				"internalType": "uint256",
				"name": "value",
				"type": "uint256"
			}
		],
		"name": "withdrawTo",
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
        self.underlyingToken: Optional[str] = None

    def toJson(self):
        return {
            'underlyingToken': self.underlyingToken if self.underlyingToken else "None",
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
            'underlying': self.underlyingToken if self.underlyingToken else "None",
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
```

# indexer/app.py

# --- Imports (Standard Libraries First) ---
import sys
import os
import re
import time
import argparse
import traceback
import yaml
import importlib
from datetime import datetime, timezone

# --- Load .env file BEFORE any other imports that might need it ---
from dotenv import load_dotenv
load_dotenv()

# --- Imports (Heavy/Custom Libraries After .env is loaded) ---
# NOTE: firebase_admin import is delayed until after FIRESTORE_EMULATOR_HOST is set
from web3 import Web3
from web3.exceptions import Web3RPCError
from state import IndexerState
from redundancy import RedundancyProtocol
import socket
import uuid

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
parser.add_argument('network', choices=['mainnet', 'testnet', 'shadownet', 'localhost', 'base-sepolia'], help="The network to run.")
parser.add_argument('app', nargs='*', default=None, choices=['homebase', 'afterme', 'trustless', 'autonet', 'all'], help="App(s) to index. One or more, or omit for 'all'. E.g. 'shadownet autonet homebase'.")

# Redundancy arguments
parser.add_argument('--mode', choices=['primary', 'secondary', 'standalone'], default='standalone',
                    help="Redundancy mode: primary, secondary, or standalone (no redundancy)")
parser.add_argument('--listen-port', type=int, default=9999,
                    help="UDP port to listen on for redundancy heartbeats")
parser.add_argument('--peer-address', type=str,
                    help="Peer address for redundancy (format: host:port)")
parser.add_argument('--instance-id', type=str,
                    help="Unique instance identifier (auto-generated if not provided)")

parser.add_argument('--skip-historical', action='store_true',
                    help="Skip historical sync and go straight to active listening mode")

parser.add_argument('--no-alerts', action='store_true',
                    help="Disable Discord alerts (useful for local testing)")

args = parser.parse_args()

def alert(message: str):
    """Helper function to safely send alerts with network/app context."""
    if CAN_SEND_ALERTS and not args.no_alerts:
        send_indexer_alert(message, network=args.network, app=args.app)

# --- Load Config to Get Enabled Apps ---
config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
try:
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    enabled_apps = config.get('enabled_apps', [])
    print(f"Loaded config.yaml: enabled_apps = {enabled_apps}")
except Exception as e:
    error_msg = f"Could not load config.yaml: {e}"
    print(f"FATAL: {error_msg}")
    alert(error_msg)
    sys.exit(1)

# --- Filter Enabled Apps Based on Command-Line Argument(s) ---
# `app` is a list: ['all'] (default) runs every enabled app; otherwise run the
# named subset, in the order given, restricted to apps enabled in config.yaml.
requested = args.app or ['all']   # omitted (None) or empty -> all
if 'all' in requested:
    apps_to_run = enabled_apps
else:
    apps_to_run = [a for a in requested if a in enabled_apps]
    missing = [a for a in requested if a not in enabled_apps]
    if missing:
        error_msg = (f"App(s) {missing} not enabled in config.yaml. "
                     f"Enabled apps: {enabled_apps}")
        print(f"FATAL: {error_msg}")
        alert(error_msg)
        sys.exit(1)

print(f"--- Indexer starting for Network: {args.network.upper()}, App(s): {', '.join(apps_to_run).upper()} ---")

# --- Network Config ---
if args.network == 'mainnet':
    default_rpc = "https://node.mainnet.etherlink.com"
elif args.network == 'testnet':
    default_rpc = "https://node.ghostnet.etherlink.com"
elif args.network == 'shadownet':
    default_rpc = "https://node.shadownet.etherlink.com"
elif args.network == 'localhost':
    default_rpc = "http://127.0.0.1:8545"  # Hardhat node
    # Set Firestore emulator host for localhost (matches Flutter apps' local test mode)
    os.environ["FIRESTORE_EMULATOR_HOST"] = "127.0.0.1:8080"
    print("Using Firestore emulator at 127.0.0.1:8080")
elif args.network == 'base-sepolia':
    default_rpc = "https://base-sepolia-rpc.publicnode.com"
else:
    error_msg = f"Invalid network '{args.network}' specified."
    print(f"FATAL: {error_msg}")
    alert(error_msg)
    sys.exit(1)

rpc = default_rpc

# --- Import Firebase AFTER setting emulator host for localhost ---
from firebase_admin import initialize_app, firestore, credentials

# --- Dynamic App Loading ---
app_modules = {}  # Store loaded app modules
app_dbs = {}  # Store Firebase DB clients per app
app_configs = {}  # Store app network configs

for app_name in apps_to_run:
    try:
        print(f"\n--- Loading app: {app_name} ---")

        # Import app config and indexer modules
        app_config_module = importlib.import_module(f'apps.{app_name}.config')
        app_indexer_module = importlib.import_module(f'apps.{app_name}.indexer')

        app_modules[app_name] = {
            'config': app_config_module,
            'indexer': app_indexer_module
        }

        # Get network-specific config
        if args.network not in app_config_module.NETWORKS:
            error_msg = f"Network '{args.network}' not found in {app_name} config"
            print(f"FATAL: {error_msg}")
            alert(error_msg)
            sys.exit(1)

        network_config = app_config_module.NETWORKS[args.network]
        app_configs[app_name] = network_config
        print(f"Network config for {app_name}: {network_config}")

        # Initialize Firebase for this app
        cred_path = app_config_module.FIREBASE_CREDENTIALS
        cred = credentials.Certificate(cred_path)

        # Use unique app name for Firebase, or 'default' if it's the only app
        firebase_app_name = f'{app_name}App' if len(apps_to_run) > 1 else 'default'
        firebase_app = initialize_app(cred, name=firebase_app_name)
        db = firestore.client(app=firebase_app)
        app_dbs[app_name] = db

        print(f"Firebase app '{firebase_app_name}' for {app_name} initialized.")

    except Exception as e:
        error_msg = f"Could not load or initialize app '{app_name}': {e}"
        print(f"FATAL: {error_msg}")
        traceback.print_exc()
        alert(error_msg)
        sys.exit(1)

# --- Fetch RPC from Firestore (check all apps, first one with RPC wins) ---
for app_name in apps_to_run:
    db = app_dbs[app_name]
    network_config = app_configs[app_name]
    doc_name = network_config.get('firestore_doc_name')

    if doc_name:
        try:
            # Try to get RPC from contracts collection first, then networks
            for collection_name in ['contracts', 'networks']:
                doc_ref = db.collection(collection_name).document(doc_name)
                doc = doc_ref.get()
                if doc.exists:
                    config_dict = doc.to_dict()
                    rpc_from_fs = config_dict.get('rpc')
                    if rpc_from_fs:
                        print(f"Using RPC from {app_name} Firestore: {rpc_from_fs}")
                        rpc = rpc_from_fs
                        break
            if rpc != default_rpc:
                break  # Found an RPC, stop checking other apps
        except Exception as e:
            print(f"Warning: Could not fetch RPC from {app_name} Firestore: {e}")

web3 = Web3(Web3.HTTPProvider(rpc))
if not web3.is_connected():
    error_msg = f"Node connection to {rpc} failed!"
    print(f"FATAL: {error_msg}")
    alert(error_msg)
    sys.exit(1)
print(f"Node connected successfully to {rpc}")

# --- Initialize Apps and Setup Event Listening ---
listening_to_addresses = []
event_signatures = {}
papers = {}
app_data = {}  # Store app-specific data (addresses, papers, collections, etc.)

# --- Dynamic App Initialization ---
for app_name in apps_to_run:
    try:
        print(f"\n=== Initializing app: {app_name} ===")
        db = app_dbs[app_name]
        network_config = app_configs[app_name]
        indexer_module = app_modules[app_name]['indexer']

        # Call app's initialize() function
        init_result = indexer_module.initialize(db, web3, network_config)
        print(f"Initialization result for {app_name}: {list(init_result.keys())}")

        # Add event signatures from this app
        app_event_sigs = init_result.get('event_signatures', {})
        event_signatures.update(app_event_sigs)
        print(f"Added {len(app_event_sigs)} event signatures from {app_name}")

        # Store app-specific data for later use
        app_data[app_name] = init_result

    except Exception as e:
        error_msg = f"Could not initialize app '{app_name}': {e}"
        print(f"FATAL: {error_msg}")
        traceback.print_exc()
        alert(error_msg)
        sys.exit(1)

# --- Sync checkpoints ---
# Each app's Firestore keeps its own lastSyncedBlock in contracts/<network doc>, so every
# app's checkpoint stays meaningful. On resume we take the lowest, so no app misses events.
def _checkpoint_refs():
    refs = []
    for name in apps_to_run:
        doc_name = app_configs.get(name, {}).get('firestore_doc_name')
        if doc_name and app_dbs.get(name) is not None:
            refs.append((name, app_dbs[name].collection("contracts").document(doc_name)))
    return refs

def read_checkpoint():
    blocks = []
    for name, ref in _checkpoint_refs():
        try:
            doc = ref.get()
            block = doc.to_dict().get('lastSyncedBlock') if doc.exists else None
        except Exception as e:
            print(f"Warning: Could not read lastSyncedBlock for {name}: {e}")
            continue
        if block:
            blocks.append(block)
    return min(blocks) if blocks else None

def write_checkpoint(block):
    for name, ref in _checkpoint_refs():
        try:
            ref.set({'lastSyncedBlock': block}, merge=True)
        except Exception as e:
            print(f"Warning: Could not update lastSyncedBlock for {name}: {e}")

# --- Historical Sync for All Apps ---
# Only run if: checkpoint exists AND --skip-historical is NOT set
if args.skip_historical:
    print("\n=== SKIPPING HISTORICAL SYNC (--skip-historical flag) ===")
else:
    # Check if checkpoint exists
    checkpoint_exists = read_checkpoint() is not None

    if not checkpoint_exists:
        print("\n=== SKIPPING HISTORICAL SYNC (no checkpoint found) ===")
    else:
        for app_name in apps_to_run:
            try:
                print(f"\n=== Running historical sync for app: {app_name} ===")
                db = app_dbs[app_name]
                network_config = app_configs[app_name]
                indexer_module = app_modules[app_name]['indexer']

                # Call app's run_historical_sync() function
                indexer_module.run_historical_sync(db, web3, network_config, alert)

            except Exception as e:
                error_msg = f"Error during {app_name} historical sync: {e}"
                print(f"ERROR: {error_msg}")
                traceback.print_exc()
                alert(error_msg)

# --- Setup Paper Objects for All Apps ---
for app_name in apps_to_run:
    try:
        print(f"\n=== Setting up papers for app: {app_name} ===")
        db = app_dbs[app_name]
        network_config = app_configs[app_name]
        indexer_module = app_modules[app_name]['indexer']

        # Call app's setup_papers() function
        papers_result = indexer_module.setup_papers(db, web3, network_config)

        # Extract addresses and papers
        app_addresses = papers_result.get('addresses', [])
        app_papers = papers_result.get('papers', {})

        listening_to_addresses.extend(app_addresses)
        papers.update(app_papers)

        # Store additional app-specific data (like collections)
        app_data[app_name].update({
            'addresses': app_addresses,
            'papers': app_papers,
            'daos_collection': papers_result.get('daos_collection'),  # For homebase
            'wills_collection_name': network_config.get('wills_collection_name'),  # For afterme
            'network_collection': papers_result.get('network_collection'),  # For trustless
        })

        print(f"Added {len(app_addresses)} addresses and {len(app_papers)} papers from {app_name}")

    except Exception as e:
        error_msg = f"Could not setup papers for app '{app_name}': {e}"
        print(f"FATAL: {error_msg}")
        traceback.print_exc()
        alert(error_msg)
        sys.exit(1)

# --- Autonet: follow every jurisdiction's Substrate ---
# Each jurisdiction with the autonet module records its Substrate in the registry; the DAO
# indexer keeps those maps, so read them once here, and the main loop adds new ones live.
def add_substrate_paper(address):
    if address in papers:
        return
    from apps.autonet.paper import Paper as AutonetPaper
    papers[address] = AutonetPaper(address, web3, app_dbs['autonet'],
                                   app_configs['autonet'].get('agents_collection', 'agents'))
    if address not in listening_to_addresses:
        listening_to_addresses.append(address)
    print(f"[autonet] Following Substrate {address}")

if 'autonet' in apps_to_run and 'homebase' in apps_to_run:
    try:
        for substrate in app_modules['autonet']['indexer'].jurisdiction_substrates(app_data['homebase']['daos_collection']):
            add_substrate_paper(substrate)
    except Exception as e:
        print(f"Warning: could not read jurisdiction Substrates: {e}")

# --- Finalize Listener Setup ---
listening_to_addresses = list(set([addr for addr in listening_to_addresses if addr]))
print("\n--- Initializing with Monitored Event Signatures ---")
for hash_val, name in event_signatures.items():
    print(f"- {name}: {hash_val}")
print("----------------------------------------------------")
print(f"Listening for {len(event_signatures)} events on {len(listening_to_addresses)} contracts.")

# --- Redundancy Setup ---
current_role = args.mode  # primary, secondary, or standalone
redundancy_enabled = args.mode in ['primary', 'secondary']
redundancy_protocol = None
state = None

if redundancy_enabled:
    # Initialize SQLite state
    state_db_path = f"indexer_state_{args.network}.db"
    state = IndexerState(state_db_path)

    # Generate instance ID if not provided
    instance_id = args.instance_id or f"indexer-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    state.set_instance_id(instance_id)
    state.set_started_as(args.mode)

    # Startup role detection: Check if we should switch roles
    # Use the first available app to check Firestore last synced block
    if apps_to_run:
        try:
            # Get local last processed block
            local_last_block = state.get_last_processed_block()

            firestore_last_block = read_checkpoint()
            if firestore_last_block is not None:
                # If Firestore is significantly ahead, we fell behind
                if firestore_last_block > local_last_block + 5:
                    print(f"\n!!! ROLE DETECTION: Firestore ahead (block {firestore_last_block}) vs local (block {local_last_block}) !!!")
                    if args.mode == 'primary':
                        print("!!! PRIMARY -> SECONDARY: Another instance took over while we were down !!!")
                        current_role = 'secondary'
                        state.set_current_role('secondary')
                        alert(f"Role changed: PRIMARY -> SECONDARY (detected newer Firestore data at block {firestore_last_block})")
                    else:
                        print("Staying as SECONDARY (expected state)")
                        state.set_current_role('secondary')
                else:
                    # We're caught up or ahead
                    state.set_current_role(args.mode)
                    print(f"Starting as {args.mode.upper()} (local block {local_last_block}, Firestore block {firestore_last_block})")
            else:
                # No Firestore state yet, use startup mode
                state.set_current_role(args.mode)
                print(f"Starting as {args.mode.upper()} (no Firestore checkpoint found)")
        except Exception as e:
            print(f"Warning: Could not perform startup role detection: {e}")
            state.set_current_role(args.mode)
    else:
        state.set_current_role(args.mode)

    # Get auth token from environment
    auth_token = os.getenv('REDUNDANCY_AUTH_TOKEN', 'default-insecure-token')
    if auth_token == 'default-insecure-token':
        print("WARNING: Using default auth token! Set REDUNDANCY_AUTH_TOKEN environment variable for production.")

    # Initialize redundancy protocol
    def on_peer_register(peer_address, peer_data):
        """Callback when peer registers."""
        print(f"✅ Peer registered: {peer_data.get('instance_id')} at {peer_address}")
        state.set_peer_address(f"{peer_address[0]}:{peer_address[1]}")
        alert(f"✅ Redundancy: Peer {peer_data.get('instance_id')} registered at {peer_address[0]}:{peer_address[1]}")

    def on_peer_heartbeat(peer_data):
        """Callback when peer sends heartbeat."""
        # Silently update (don't spam logs)
        pass

    def on_peer_failure():
        """Callback when peer fails (no heartbeat)."""
        global current_role
        if current_role == 'secondary':
            print("\n!!! PEER FAILURE DETECTED: Primary is down !!!")
            print("!!! SECONDARY -> PRIMARY: Promoting to active indexer !!!")
            current_role = 'primary'
            state.set_current_role('primary')
            alert("🚨 FAILOVER: Secondary promoted to PRIMARY (peer heartbeat timeout)")

    redundancy_protocol = RedundancyProtocol(
        listen_port=args.listen_port,
        auth_token=auth_token,
        instance_id=instance_id,
        on_register=on_peer_register,
        on_heartbeat=on_peer_heartbeat,
        on_peer_failure=on_peer_failure
    )
    redundancy_protocol.start()

    # If secondary, register with primary
    if current_role == 'secondary' and args.peer_address:
        try:
            peer_host, peer_port = args.peer_address.split(':')
            peer_port = int(peer_port)
            print(f"Registering with primary at {peer_host}:{peer_port}...")
            redundancy_protocol.register_with_peer(peer_host, peer_port, args.listen_port)
        except Exception as e:
            print(f"Error registering with peer: {e}")
            alert(f"⚠️ Failed to register with peer at {args.peer_address}: {e}")

    print(f"\n=== Redundancy Enabled: Role={current_role.upper()}, ListenPort={args.listen_port} ===\n")
else:
    print("\n=== Redundancy Disabled: Running in STANDALONE mode ===\n")

# --- Main Indexing Loop ---
heartbeat = 0
# --- START OF CHANGE: Stateful Indexing Logic ---
# Some RPCs (like Base Sepolia public nodes) limit the number of addresses per query,
# so addresses are queried in batches and the results merged in chain order.
MAX_ADDRESSES_PER_QUERY = 5

def log_position(log_entry):
    return (log_entry['blockNumber'], log_entry['transactionIndex'], log_entry['logIndex'])

DAO_OVERRULE_SELECTOR = Web3.keccak(text="daoOverrule(uint256,string)")[:4]
DAO_VETO_SELECTOR = Web3.keccak(text="daoVeto()")[:4]
PROPOSAL_CREATED_TYPES = ["uint256", "address", "address[]", "uint256[]", "string[]", "bytes[]", "uint256", "uint256", "string"]

# Registers DAO proposals on the projects they veto or overrule, so the project holds signing and
# finalizing until the proposal resolves. Anyone may register; this is the automatic path. The key
# only pays gas: INDEXER_RELAYER_KEY, or on localhost hardhat's public account #9.
HARDHAT_ACCOUNT_9 = "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6"
relayer_key = os.getenv("INDEXER_RELAYER_KEY") or (HARDHAT_ACCOUNT_9 if args.network == "localhost" else None)
if not relayer_key:
    print("No INDEXER_RELAYER_KEY: DAO proposals won't be registered on projects automatically")

def register_on_project(project, targets, values, calldatas, description):
    from apps.trustless.abis import register_dao_proposal_abi
    account = web3.eth.account.from_key(relayer_key)
    contract = web3.eth.contract(address=project, abi=register_dao_proposal_abi)
    fn = contract.functions.registerDaoProposal(targets, values, calldatas, Web3.keccak(text=description))
    try:
        fn.call({"from": account.address})
    except Exception as e:
        # Already registered by someone, closed, no longer live: nothing to do
        print(f"[trustless] Not registering on {project}: {e}")
        return
    tx = fn.build_transaction({"from": account.address, "nonce": web3.eth.get_transaction_count(account.address, "pending")})
    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"[trustless] Registered DAO proposal on {project}: status {receipt.status}, tx {tx_hash.hex()}")

def notify_appeal_proposals(log_entry, papers):
    """A DAO proposal that calls daoVeto or daoOverrule on a trustless project: an overrule is that
    project's appeal (shown on the project), and either is registered on the project so it holds."""
    try:
        from eth_abi import decode
        proposal_id, _, targets, values, _, calldatas, _, _, description = decode(PROPOSAL_CREATED_TYPES, bytes(log_entry["data"]))
        projects = []
        for target, calldata in zip(targets, calldatas):
            address = Web3.to_checksum_address(target)
            paper = papers.get(address)
            if getattr(paper, "kind", None) != "project":
                continue
            if calldata[:4] == DAO_OVERRULE_SELECTOR:
                paper.handle_appeal_proposal(log_entry, proposal_id)
            if calldata[:4] in (DAO_OVERRULE_SELECTOR, DAO_VETO_SELECTOR) and address not in projects:
                projects.append(address)
        if relayer_key:
            checksummed = [Web3.to_checksum_address(t) for t in targets]
            for project in projects:
                try:
                    register_on_project(project, checksummed, list(values), list(calldatas), description)
                except Exception as e:
                    print(f"[trustless] Could not register proposal {proposal_id} on {project}: {e}")
                    alert(f"Could not register DAO proposal {proposal_id} on project {project}: {e}")
    except Exception as e:
        print(f"[trustless] Could not inspect proposal for appeals: {e}")

def fetch_logs(addresses, from_block, to_block):
    if len(addresses) <= MAX_ADDRESSES_PER_QUERY:
        return list(web3.eth.get_logs({"fromBlock": from_block, "toBlock": to_block, "address": list(addresses)}))
    found = []
    num_batches = (len(addresses) + MAX_ADDRESSES_PER_QUERY - 1) // MAX_ADDRESSES_PER_QUERY
    for i in range(0, len(addresses), MAX_ADDRESSES_PER_QUERY):
        found.extend(web3.eth.get_logs({"fromBlock": from_block, "toBlock": to_block, "address": addresses[i:i + MAX_ADDRESSES_PER_QUERY]}))
        # Small delay between batches to avoid rate limiting (50ms per batch)
        if num_batches > 10 and i + MAX_ADDRESSES_PER_QUERY < len(addresses):
            time.sleep(0.05)
    found.sort(key=log_position)
    return found

last_processed_block = -1  # Sentinel for uninitialized state
BLOCK_HEADROOM = 3  # Query logs 3 blocks behind the head of the chain for safety
MAX_BLOCK_RANGE = 500 # Process a maximum of 500 blocks at a time to not overload the RPC

while True:
    heartbeat += 1
    try:
        if not listening_to_addresses:
            print("No contracts to listen to. Waiting...")
            time.sleep(15)
            continue

        latest_on_chain = web3.eth.block_number

        # Initialize our block tracker on the first run.
        if last_processed_block == -1:
            if args.skip_historical:
                # Skip historical = start from current block (minus small buffer)
                last_processed_block = max(0, latest_on_chain - 15)
                print(f"Skipping historical sync. Starting from block {last_processed_block}.")
            else:
                checkpoint_block = read_checkpoint()

                # Use checkpoint if available, otherwise start from latest - 15
                if checkpoint_block is not None and checkpoint_block > 0:
                    last_processed_block = checkpoint_block
                    print(f"Resuming from checkpoint. Starting from block {last_processed_block}.")
                else:
                    last_processed_block = max(0, latest_on_chain - 15)
                    print(f"No checkpoint found. Starting from block {last_processed_block}.")
            continue

        # Determine the range of blocks to scan in this iteration
        from_block = last_processed_block + 1
        to_block = max(0, latest_on_chain - BLOCK_HEADROOM)

        # If we are already caught up, just wait.
        if from_block > to_block:
            time.sleep(5)
            continue

        # If the indexer has been offline, process in manageable chunks.
        if (to_block - from_block) > MAX_BLOCK_RANGE:
            to_block = from_block + MAX_BLOCK_RANGE - 1
            print(f"[{args.network.upper()}] Large gap detected. Processing chunk: {from_block} -> {to_block}")

        logs = fetch_logs(listening_to_addresses, from_block, to_block)

        if logs:
            print(f"[{args.network.upper()}] Found {len(logs)} logs between blocks {from_block} and {to_block}")

        # Contracts discovered mid-batch (new DAOs, tokens, economies, projects) may emit events
        # later in this same range, so their logs are fetched and merged into the queue as found.
        pending_logs = list(logs)
        handled_logs = set()
        while pending_logs:
            log_entry = pending_logs.pop(0)
            log_key = (log_entry["transactionHash"], log_entry["logIndex"])
            if log_key in handled_logs:
                continue
            handled_logs.add(log_key)
            known_before = set(listening_to_addresses)
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

            # Secondary instances monitor events but don't write to Firestore
            if current_role == 'secondary':
                continue  # Skip processing, just monitor

            new_contract_info = papers[contract_address].handle_event(log_entry, func=event_name)
            if event_name == 'ProposalCreated' and 'trustless' in apps_to_run:
                notify_appeal_proposals(log_entry, papers)
            
            # Handle new contract events dynamically per app
            if new_contract_info == "DELETED":
                # AfterMe: Will was cleared
                print(f"Removing cleared will {contract_address} from active listeners.")
                if contract_address in listening_to_addresses: listening_to_addresses.remove(contract_address)
                if contract_address in papers: del papers[contract_address]
                print(f"Now listening to {len(listening_to_addresses)} addresses.")
            elif isinstance(new_contract_info, list) and len(new_contract_info) in (2, 3) and all(isinstance(x, str) for x in new_contract_info):
                # Homebase: New DAO created (returns [dao_address, token_address, registry_address])
                if 'homebase' in apps_to_run:
                    dao_address_new, token_address_new = new_contract_info[:2]
                    registry_address_new = Web3.to_checksum_address(new_contract_info[2]) if len(new_contract_info) == 3 and new_contract_info[2] else None
                    print(f"Adding new Homebase DAO {dao_address_new} and Token {token_address_new} to listener.")
                    if dao_address_new not in listening_to_addresses: listening_to_addresses.append(dao_address_new)
                    if token_address_new not in listening_to_addresses: listening_to_addresses.append(token_address_new)

                    # Get homebase-specific data
                    homebase_db = app_dbs['homebase']
                    homebase_data = app_data['homebase']
                    daos_collection = homebase_data.get('daos_collection')

                    # Import homebase Paper class
                    from apps.homebase.paper import Paper as HomebasePaper

                    if token_address_new not in papers:
                        p_new_token = HomebasePaper(address=token_address_new, kind="token", daos_collection=daos_collection, db=homebase_db, dao=dao_address_new, web3=web3)
                        papers.update({token_address_new: p_new_token})
                    else:
                        p_new_token = papers[token_address_new]
                    if dao_address_new not in papers:
                        papers.update({dao_address_new: HomebasePaper(token=p_new_token, address=dao_address_new, kind="dao", daos_collection=daos_collection, db=homebase_db, dao=dao_address_new, web3=web3)})
                    if registry_address_new and registry_address_new not in papers:
                        listening_to_addresses.append(registry_address_new)
                        papers.update({registry_address_new: HomebasePaper(address=registry_address_new, kind="registry", daos_collection=daos_collection, db=homebase_db, dao=dao_address_new, web3=web3,
                                                                           trustless_db=homebase_data.get('trustless_db'),
                                                                           trustless_network_collection=app_configs['homebase'].get('trustless_network_collection'))})
                    print(f"Now listening to {len(listening_to_addresses)} addresses.")
            elif isinstance(new_contract_info, list) and all(isinstance(item, tuple) for item in new_contract_info):
                # Homebase: SuiteConfigured returns list of tuples: [('economy', addr), ('registry', addr, dao_addr)];
                # a registry's jurisdiction.economy entry returns [('economy', addr)]
                for item in new_contract_info:
                    if item[0] == 'economy' and 'trustless' in apps_to_run:
                        economy_address_new = Web3.to_checksum_address(item[1])
                        print(f"Adding new Trustless Economy {economy_address_new} to listener.")
                        if economy_address_new not in listening_to_addresses: listening_to_addresses.append(economy_address_new)

                        if economy_address_new not in papers:
                            trustless_db = app_dbs['trustless']
                            trustless_data = app_data['trustless']
                            network_collection = trustless_data.get('network_collection')
                            from apps.trustless.paper import Paper as TrustlessPaper
                            papers.update({economy_address_new: TrustlessPaper(
                                address=economy_address_new,
                                kind="economy",
                                web3=web3,
                                db=trustless_db,
                                network_collection=network_collection,
                            )})
                        print(f"Now listening to {len(listening_to_addresses)} addresses.")
                    elif item[0] == 'substrate' and 'autonet' in apps_to_run:
                        add_substrate_paper(item[1])
                    elif item[0] == 'registry' and 'homebase' in apps_to_run:
                        # Registry for Economy DAO description updates
                        registry_address_new = Web3.to_checksum_address(item[1])
                        dao_address_for_registry = Web3.to_checksum_address(item[2])
                        print(f"Adding new Registry {registry_address_new} (DAO: {dao_address_for_registry[:10]}...) to listener.")
                        if registry_address_new not in listening_to_addresses: listening_to_addresses.append(registry_address_new)

                        if registry_address_new not in papers:
                            homebase_db = app_dbs['homebase']
                            homebase_data = app_data['homebase']
                            daos_collection = homebase_data.get('daos_collection')
                            from apps.homebase.paper import Paper as HomebasePaper
                            papers.update({registry_address_new: HomebasePaper(
                                address=registry_address_new,
                                kind="registry",
                                daos_collection=daos_collection,
                                db=homebase_db,
                                dao=dao_address_for_registry,
                                web3=web3,
                                trustless_db=homebase_data.get('trustless_db'),
                                trustless_network_collection=app_configs['homebase'].get('trustless_network_collection'),
                            )})
                        print(f"Now listening to {len(listening_to_addresses)} addresses.")
            elif isinstance(new_contract_info, str) and new_contract_info.startswith("0x"):
                # Handle single address return from AfterMe or Trustless
                # Determine which app based on the emitting paper's kind

                emitting_paper = papers.get(contract_address)
                emitting_kind = getattr(emitting_paper, 'kind', None) if emitting_paper else None

                # Trustless: New project created (economy emits NewProject)
                if 'trustless' in apps_to_run and emitting_kind == 'economy':
                    new_project_address = Web3.to_checksum_address(new_contract_info)
                    # contract_address IS the economy that emitted the NewProject event
                    economy_address = contract_address
                    print(f"Adding new Trustless Project {new_project_address} (economy: {economy_address[:10]}...) to listener.")
                    if new_project_address not in listening_to_addresses: listening_to_addresses.append(new_project_address)
                    if new_project_address not in papers:
                        # Get trustless-specific data
                        trustless_db = app_dbs['trustless']
                        trustless_data = app_data['trustless']
                        network_collection = trustless_data.get('network_collection')

                        # Import trustless Paper class
                        from apps.trustless.paper import Paper as TrustlessPaper

                        papers.update({new_project_address: TrustlessPaper(
                            address=new_project_address,
                            kind="project",
                            web3=web3,
                            db=trustless_db,
                            network_collection=network_collection,
                            economy_address=economy_address,
                        )})
                    print(f"Now listening to {len(listening_to_addresses)} addresses.")

                # AfterMe: New will created (returns will address)
                elif 'afterme' in apps_to_run:
                    new_will_address = new_contract_info
                    print(f"Adding new AfterMe Will {new_will_address} to listener.")
                    if new_will_address not in listening_to_addresses: listening_to_addresses.append(new_will_address)
                    if new_will_address not in papers:
                        # Get afterme-specific data
                        afterme_db = app_dbs['afterme']
                        afterme_data = app_data['afterme']
                        wills_collection_name = afterme_data.get('wills_collection_name')

                        # Import afterme Paper class
                        from apps.afterme.paper import Paper as AftermePaper

                        papers.update({new_will_address: AftermePaper(address=new_will_address, kind="afterme_will", db=afterme_db, web3=web3, wills_collection_name=wills_collection_name)})
                    print(f"Now listening to {len(listening_to_addresses)} addresses.")

            discovered = [a for a in listening_to_addresses if a not in known_before]
            if discovered:
                # Include the whole discovering transaction: a project's initial SendFunds is emitted
                # before the economy's NewProject in the same tx.
                tx_start = (log_entry['blockNumber'], log_entry['transactionIndex'], -1)
                later_logs = [l for l in fetch_logs(discovered, log_entry['blockNumber'], to_block)
                              if log_position(l) > tx_start and (l["transactionHash"], l["logIndex"]) not in handled_logs]
                if later_logs:
                    print(f"Fetched {len(later_logs)} later logs for {len(discovered)} newly discovered contract(s).")
                    pending_logs = sorted(pending_logs + later_logs, key=log_position)

        # IMPORTANT: Update our checkpoint after a successful run
        last_processed_block = to_block

        # Update SQLite state (always, even if secondary)
        if state:
            state.set_last_processed_block(last_processed_block)

        # Update Firestore checkpoint - ONLY if we're primary (or standalone)
        should_write_to_firestore = (current_role in ['primary', 'standalone'])

        # Update checkpoint if we found events OR every 50 heartbeats to prevent stale checkpoints
        should_update_checkpoint = logs or (heartbeat % 50 == 0)

        if apps_to_run and should_update_checkpoint and should_write_to_firestore:
            write_checkpoint(last_processed_block)

        # Send heartbeat to peer
        if redundancy_protocol and current_role in ['primary', 'secondary']:
            try:
                redundancy_protocol.send_heartbeat(current_role, last_processed_block)
            except Exception as e:
                print(f"Warning: Could not send heartbeat: {e}")
        
    except Web3RPCError as e:
        # This is a more robust way to catch recoverable errors without crashing
        error_message = str(e).lower()
        is_recoverable_error = any(phrase in error_message for phrase in ["block range", "unavailable", "block limit", "server error", "rate limit", "too many requests"])
        if is_recoverable_error:
            # Parse retry time from rate limit messages like "retry in 10m0s" or "retry in 30s"
            wait_time = 10  # Default wait time
            retry_match = re.search(r'retry in (\d+)m?(\d*)s?', str(e))
            if retry_match:
                minutes = int(retry_match.group(1)) if retry_match.group(1) else 0
                seconds = int(retry_match.group(2)) if retry_match.group(2) else 0
                # If the first capture is minutes (has 'm' after), parse accordingly
                if 'm' in str(e)[retry_match.start():retry_match.end()]:
                    wait_time = minutes * 60 + seconds
                else:
                    # Just seconds (e.g., "retry in 10s")
                    wait_time = minutes  # The first number is actually seconds
                # Cap at 10 minutes max, minimum 10 seconds
                wait_time = max(10, min(wait_time, 600))
            print(f"WARN: Recoverable RPC error encountered: {e}. Waiting {wait_time}s before retry.")
            time.sleep(wait_time)
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
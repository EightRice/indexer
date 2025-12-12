# apps/afterme/indexer.py
# AfterMe app-specific indexing logic

import time
import re
from web3 import Web3
from web3.exceptions import Web3RPCError
from apps.afterme.paper import Paper as AftermePaper
from apps.afterme.abis import source_abi
from apps.afterme.config import EVENT_SIGNATURES

def initialize(db, web3, network_config):
    """
    Initialize AfterMe indexer.

    Returns:
        dict with:
            - source_address: str - AfterMe source contract address
            - event_signatures: dict - Event signature hashes mapped to event names
    """
    doc_name = network_config['firestore_doc_name']

    # Get AfterMe config from Firestore
    afterme_networks = db.collection("networks")
    afterme_doc = afterme_networks.document(doc_name).get()

    if not afterme_doc.exists:
        raise Exception(f"AfterMe config document '{doc_name}' not found in Firestore")

    afterme_config = afterme_doc.to_dict()
    source_address = afterme_config['sourceContractAddress']

    print(f"AfterMe Source address: {source_address}")

    # Build event signature mappings
    event_sigs = {}
    for sig_string in EVENT_SIGNATURES:
        event_name = sig_string.split('(')[0]
        sig_hash = web3.keccak(text=sig_string).hex()
        event_sigs[sig_hash] = event_name

    return {
        'source_address': source_address,
        'event_signatures': event_sigs,
        'config': afterme_config
    }


def run_historical_sync(db, web3, network_config, alert_func):
    """
    Run historical sync for AfterMe.
    Scans for WillCreated and WillCleared events and syncs will state.

    Args:
        db: Firestore database client
        web3: Web3 instance
        network_config: Network configuration dict
        alert_func: Function to call for alerts
    """
    doc_name = network_config['firestore_doc_name']
    wills_collection_name = network_config['wills_collection_name']

    print("\n--- Starting AfterMe Historical Sync ---")

    try:
        # Get AfterMe config from Firestore
        afterme_doc_ref = db.collection("networks").document(doc_name)
        afterme_doc = afterme_doc_ref.get()

        if not afterme_doc.exists:
            print("WARNING: AfterMe config document not found. Skipping historical sync.")
            return

        afterme_config = afterme_doc.to_dict()
        afterme_source_address = afterme_config['sourceContractAddress']

        REORG_SAFETY_MARGIN = 100
        from_block_config = afterme_config.get('fromBlock', 0)
        last_synced_block = afterme_config.get('lastSyncedBlock', 0)
        start_block = max(from_block_config, last_synced_block - REORG_SAFETY_MARGIN)

        if last_synced_block > 0:
            print(f"Checkpoint found: {last_synced_block}. Applying safety margin, starting from {start_block}.")

        temp_source_contract = web3.eth.contract(
            address=Web3.to_checksum_address(afterme_source_address),
            abi=re.sub(r'\n+', ' ', source_abi).strip()
        )

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
                    created_logs.extend(temp_source_contract.events.WillCreated.get_logs(
                        from_block=from_block_scan, to_block=to_block
                    ))
                    cleared_logs.extend(temp_source_contract.events.WillCleared.get_logs(
                        from_block=from_block_scan, to_block=to_block
                    ))
                    from_block_scan = to_block + 1
                    time.sleep(0.2)
                except Web3RPCError as e:
                    error_message = str(e).lower()
                    is_range_error = any(phrase in error_message for phrase in [
                        "block range is too large",
                        "cannot request logs over more than",
                        "block limit"
                    ])
                    if not is_range_error:
                        try:
                            is_range_error = e.args[0]['code'] in [-32062, -32603]
                        except (TypeError, KeyError, IndexError):
                            pass
                    if is_range_error:
                        print(f"    - Block range limit hit. Reducing chunk size from {current_chunk_size} and retrying.")
                        current_chunk_size //= 2
                        if current_chunk_size < 1:
                            raise Exception("Chunk size fell to zero.")
                    else:
                        raise e

            wills_collection = db.collection(wills_collection_name)
            wills_in_db_docs = list(wills_collection.stream())
            wills_in_db = {doc.id for doc in wills_in_db_docs}
            print(f"Found {len(wills_in_db)} wills in Firestore before sync.")

            created_in_range = {Web3.to_checksum_address(log['args']['willAddress']) for log in created_logs}
            cleared_in_range = {Web3.to_checksum_address(log['args']['willAddress']) for log in cleared_logs}

            master_will_list = (wills_in_db.union(created_in_range)) - cleared_in_range
            print(f"Reconciling state for {len(master_will_list)} active wills...")

            temp_paper_for_sync = AftermePaper(
                address="",
                kind="",
                db=db,
                web3=web3,
                wills_collection_name=wills_collection_name
            )

            for will_addr in master_will_list:
                print(f"Syncing will: {will_addr}")
                try:
                    will_data = temp_paper_for_sync.get_onchain_will_data(will_addr)
                    if will_data:
                        wills_collection.document(will_addr).set(will_data, merge=True)
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
        import traceback
        traceback.print_exc()
        alert_func(error_msg)
        print("WARNING: Continuing without full historical sync.")


def setup_papers(db, web3, network_config):
    """
    Create Paper objects for AfterMe source contract and all existing wills.

    Returns:
        dict with:
            - addresses: list - Addresses to listen to (source + all wills)
            - papers: dict - Paper objects mapped by address
    """
    doc_name = network_config['firestore_doc_name']
    wills_collection_name = network_config['wills_collection_name']

    # Get source address from Firestore
    afterme_doc = db.collection("networks").document(doc_name).get()
    if not afterme_doc.exists:
        raise Exception(f"AfterMe config document '{doc_name}' not found")

    afterme_config = afterme_doc.to_dict()
    source_address = afterme_config['sourceContractAddress']

    wills_collection = db.collection(wills_collection_name)
    afterme_docs = list(wills_collection.stream())
    will_addresses = [doc.id for doc in afterme_docs]

    # Create Paper objects
    papers = {}
    addresses = [source_address]

    # Source contract paper
    papers[source_address] = AftermePaper(
        address=source_address,
        kind="afterme_source",
        db=db,
        web3=web3,
        wills_collection_name=wills_collection_name
    )

    # Will papers
    for will_address in will_addresses:
        addresses.append(will_address)
        papers[will_address] = AftermePaper(
            address=will_address,
            kind="afterme_will",
            db=db,
            web3=web3,
            wills_collection_name=wills_collection_name
        )

    return {
        'addresses': addresses,
        'papers': papers
    }

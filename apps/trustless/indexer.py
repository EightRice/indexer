# apps/trustless/indexer.py

import time
from web3 import Web3

from apps.trustless.config import (
    ECONOMY_EVENT_SIGNATURES,
    PROJECT_EVENT_SIGNATURES,
    EVENT_SIGNATURES,
    NETWORKS
)
from apps.trustless.paper import Paper


def initialize(db, web3: Web3, network_config: dict):
    """
    Called once at startup.
    Returns dict with event_signatures mapping hash -> event name.
    """
    print("[trustless] Initializing...")

    # Hash all event signatures
    event_sigs = {}
    for sig_string in EVENT_SIGNATURES:
        event_name = sig_string.split('(')[0]
        sig_hash = web3.keccak(text=sig_string).hex()
        event_sigs[sig_hash] = event_name
        print(f"[trustless]   {event_name}: {sig_hash[:20]}...")

    print(f"[trustless] Initialized with {len(event_sigs)} event signatures")

    return {
        'event_signatures': event_sigs,
    }


def run_historical_sync(db, web3: Web3, network_config: dict, alert_func):
    """
    Full historical sync with optimizations:
    1. Use global checkpoint from contracts collection
    2. Scan all economies for NewProject events
    3. Batch scan ALL project addresses together (1 RPC call per chunk instead of N)
    4. Final state verification from contract reads
    """
    from apps.trustless.abis import projectReadAbi
    import json

    network_name = network_config.get('network_collection', 'Etherlink-Testnet')
    doc_name = network_config.get('firestore_doc_name', network_name)
    print(f"[trustless] Running historical sync for {network_name}...")

    # Stage enum mapping
    STAGE_MAP = {
        0: 'open', 1: 'pending', 2: 'ongoing', 3: 'dispute',
        4: 'appealable', 5: 'appeal', 6: 'closed',
    }

    # Build event signature hashes
    economy_sig_hashes = [web3.keccak(text=sig).hex() for sig in ECONOMY_EVENT_SIGNATURES]
    project_sig_hashes = [web3.keccak(text=sig).hex() for sig in PROJECT_EVENT_SIGNATURES]

    # Get global checkpoint from contracts collection
    current_block = web3.eth.block_number
    checkpoint = 0
    try:
        contracts_doc = db.collection("contracts").document(doc_name).get()
        if contracts_doc.exists:
            checkpoint = contracts_doc.to_dict().get('lastSyncedBlock', 0)
    except Exception as e:
        print(f"[trustless] Could not read checkpoint: {e}")

    from_block = checkpoint + 1 if checkpoint > 0 else max(0, current_block - 100000)

    if from_block >= current_block:
        print(f"[trustless] Already synced to block {checkpoint}")
        return

    print(f"[trustless] Syncing from block {from_block} to {current_block}")

    # Discover all economy addresses
    economy_addresses = []
    network_docs = list(db.collection(network_name).stream())
    for doc in network_docs:
        doc_id = doc.id
        if doc_id.startswith('0x') and len(doc_id) == 42:
            try:
                economy_addresses.append(Web3.to_checksum_address(doc_id))
            except:
                pass

    if not economy_addresses:
        print(f"[trustless] No economy addresses found in {network_name} collection")
        return

    print(f"[trustless] Found {len(economy_addresses)} economy address(es)")

    read_abi = json.loads(projectReadAbi)

    # Build economy papers and collect all project addresses
    economy_papers = {}
    all_project_papers = {}
    all_project_addresses = []

    for economy_address in economy_addresses:
        economy_papers[economy_address] = Paper(
            address=economy_address,
            kind='economy',
            web3=web3,
            db=db,
            network_collection=network_name,
        )

        # Load existing projects for this economy
        projects_ref = db.collection(network_name).document(economy_address.lower()).collection("projects")
        for project_doc in projects_ref.stream():
            addr = Web3.to_checksum_address(project_doc.id)
            all_project_addresses.append(addr)
            all_project_papers[addr] = Paper(
                address=addr,
                kind='project',
                web3=web3,
                db=db,
                network_collection=network_name,
                economy_address=economy_address,
            )

    print(f"[trustless] Loaded {len(all_project_addresses)} existing projects across all economies")

    # Scan blocks - all economies + all projects in batched queries
    chunk_size = 500
    processed_from = from_block
    new_projects = 0
    events_processed = 0

    while processed_from < current_block:
        to_block = min(processed_from + chunk_size - 1, current_block)

        try:
            # Query all economies for NewProject events
            economy_logs = web3.eth.get_logs({
                'fromBlock': processed_from,
                'toBlock': to_block,
                'address': economy_addresses,
            })

            for log in economy_logs:
                topic0 = log['topics'][0].hex() if log['topics'] else None
                if topic0 in economy_sig_hashes:
                    log_economy = Web3.to_checksum_address(log['address'])
                    if log_economy in economy_papers:
                        result = economy_papers[log_economy].handle_event(log, func='NewProject')
                        if result:
                            new_addr = Web3.to_checksum_address(result)
                            if new_addr not in all_project_papers:
                                all_project_addresses.append(new_addr)
                                all_project_papers[new_addr] = Paper(
                                    address=new_addr,
                                    kind='project',
                                    web3=web3,
                                    db=db,
                                    network_collection=network_name,
                                    economy_address=log_economy,
                                )
                                new_projects += 1

            # Query ALL project addresses in ONE call (batched)
            if all_project_addresses:
                project_logs = web3.eth.get_logs({
                    'fromBlock': processed_from,
                    'toBlock': to_block,
                    'address': all_project_addresses,
                })

                for log in project_logs:
                    topic0 = log['topics'][0].hex() if log['topics'] else None
                    if topic0 in project_sig_hashes:
                        log_address = Web3.to_checksum_address(log['address'])
                        if log_address in all_project_papers:
                            idx = project_sig_hashes.index(topic0)
                            event_name = PROJECT_EVENT_SIGNATURES[idx].split('(')[0]
                            all_project_papers[log_address].handle_event(log, func=event_name)
                            events_processed += 1

        except Exception as e:
            print(f"[trustless] Error at blocks {processed_from}-{to_block}: {e}")

        processed_from = to_block + 1
        time.sleep(0.02)

    print(f"[trustless] Scanned: {new_projects} new projects, {events_processed} events processed")

    # Final state verification for all projects
    print(f"[trustless] Verifying final state...")
    verified = 0
    for project_addr in all_project_addresses:
        try:
            contract = web3.eth.contract(address=project_addr, abi=read_abi)
            contract_stage_int = contract.functions.stage().call()
            contract_stage = STAGE_MAP.get(contract_stage_int, 'unknown')

            # Get economy address for this project
            economy_addr = all_project_papers[project_addr].economy_address
            projects_ref = db.collection(network_name).document(economy_addr.lower()).collection("projects")

            doc = projects_ref.document(project_addr.lower()).get()
            if doc.exists:
                fs_stage = doc.to_dict().get('stage', '')
                if fs_stage != contract_stage:
                    projects_ref.document(project_addr.lower()).update({'stage': contract_stage})
                    print(f"[trustless]   Fixed {project_addr[:10]}...: {fs_stage} -> {contract_stage}")
            verified += 1
        except Exception as e:
            pass
        time.sleep(0.01)

    print(f"[trustless] Verified {verified} projects")

    # Update global checkpoint in contracts collection
    try:
        db.collection("contracts").document(doc_name).set(
            {'lastSyncedBlock': current_block}, merge=True
        )
        print(f"[trustless] Updated checkpoint to block {current_block}")
    except Exception as e:
        print(f"[trustless] Could not update checkpoint: {e}")

    print(f"[trustless] Historical sync complete")


def setup_papers(db, web3: Web3, network_config: dict):
    """
    Creates Paper objects for all existing contracts.
    Returns dict with addresses and papers.

    Firestore structure:
    - {networkName}/{economyAddress}/ → economy documents (ID is the 0x address)
        - projects/{projectAddress}
    """
    network_name = network_config.get('network_collection', 'Etherlink-Testnet')
    print(f"[trustless] Setting up papers for {network_name}...")

    # Discover all economy addresses by scanning the network collection
    economy_addresses = []

    network_docs = db.collection(network_name).stream()
    for doc in network_docs:
        doc_id = doc.id
        # Check if doc ID looks like an Ethereum address (0x + 40 hex chars)
        if doc_id.startswith('0x') and len(doc_id) == 42:
            try:
                addr = Web3.to_checksum_address(doc_id)
                economy_addresses.append(addr)
            except:
                pass

    if not economy_addresses:
        print(f"[trustless] No economy addresses found in {network_name} collection")
        return {
            'addresses': [],
            'papers': {},
            'economy_addresses': [],
            'network_collection': network_name,
        }

    print(f"[trustless] Found {len(economy_addresses)} economy address(es)")

    addresses = []
    papers = {}

    # Process each economy
    for economy_address in economy_addresses:
        addresses.append(economy_address)

        # Create economy paper
        papers[economy_address] = Paper(
            address=economy_address,
            kind='economy',
            web3=web3,
            db=db,
            network_collection=network_name,
        )

        # Load existing projects from Firestore for this economy
        projects_collection = (db.collection(network_name)
                              .document(economy_address.lower())
                              .collection("projects"))

        project_count = 0
        for project_doc in projects_collection.stream():
            project_addr = Web3.to_checksum_address(project_doc.id)
            addresses.append(project_addr)
            papers[project_addr] = Paper(
                address=project_addr,
                kind='project',
                web3=web3,
                db=db,
                network_collection=network_name,
                economy_address=economy_address,
            )
            project_count += 1

        print(f"[trustless] Economy {economy_address[:10]}... has {project_count} projects")

    print(f"[trustless] Setup complete: {len(addresses)} total addresses ({len(economy_addresses)} economies)")

    return {
        'addresses': addresses,
        'papers': papers,
        'economy_addresses': economy_addresses,
        'network_collection': network_name,
    }

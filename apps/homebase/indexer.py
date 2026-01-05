# apps/homebase/indexer.py
# Homebase app-specific indexing logic

import os
import time
from web3 import Web3
from web3.exceptions import Web3RPCError
from apps.homebase.paper import Paper as HomebasePaper
from apps.homebase.config import EVENT_SIGNATURES, TRUSTLESS_FIREBASE_CREDENTIALS

# Trustless Firebase app instance (initialized once)
_trustless_db = None


def _init_trustless_firebase():
    """
    Initialize Trustless Firebase app for cross-writing Economy documents.
    Returns the Firestore client or None if initialization fails.
    """
    global _trustless_db
    if _trustless_db is not None:
        return _trustless_db

    try:
        from firebase_admin import initialize_app, firestore, credentials

        cred_path = TRUSTLESS_FIREBASE_CREDENTIALS
        if not os.path.isabs(cred_path):
            # Make path relative to indexer root
            indexer_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            cred_path = os.path.join(indexer_root, cred_path)

        if not os.path.exists(cred_path):
            print(f"WARNING: Trustless Firebase credentials not found at {cred_path}. Economy cross-write disabled.")
            return None

        cred = credentials.Certificate(cred_path)
        trustless_app = initialize_app(cred, name='trustlessFromHomebase')
        _trustless_db = firestore.client(app=trustless_app)
        print(f"Trustless Firebase initialized successfully for cross-writing Economy documents.")
        return _trustless_db
    except Exception as e:
        print(f"WARNING: Could not initialize Trustless Firebase: {e}. Economy cross-write disabled.")
        return None


def initialize(db, web3, network_config):
    """
    Initialize Homebase indexer.

    Returns:
        dict with:
            - wrapper_address: str - Main wrapper contract address
            - wrapper_w_address: str - Wrapped wrapper address
            - wrapper_trustless_address: str or None - Trustless wrapper address
            - event_signatures: dict - Event signature hashes mapped to event names
            - trustless_db: Firestore client for Trustless or None
    """
    doc_name = network_config['firestore_doc_name']

    # Get Homebase config from Firestore
    homebase_networks = db.collection("contracts")
    homebase_doc = homebase_networks.document(doc_name).get()

    if not homebase_doc.exists:
        raise Exception(f"Homebase config document '{doc_name}' not found in Firestore")

    homebase_config = homebase_doc.to_dict()
    # Load all wrapper types
    wrapper_address = homebase_config.get('wrapper') or homebase_config.get('wrapper_jurisdiction')
    wrapper_t_address = homebase_config.get('wrapper_t')  # Transferable non-wrapped
    wrapper_w_address = homebase_config.get('wrapper_w')  # Wrapped ERC20 tokens
    wrapper_trustless_address = homebase_config.get('wrapper_trustless', None)

    print(f"Homebase Wrapper (NON-TRANSFERABLE): {wrapper_address}")
    if wrapper_t_address:
        print(f"Homebase Wrapper_t (TRANSFERABLE): {wrapper_t_address}")
    if wrapper_w_address:
        print(f"Homebase Wrapper_w (WRAPPED ERC20): {wrapper_w_address}")
    if wrapper_trustless_address:
        print(f"Homebase Trustless Wrapper address: {wrapper_trustless_address}")

    # Initialize Trustless Firebase for cross-writing Economy documents
    trustless_db = _init_trustless_firebase()

    # Build event signature mappings
    event_sigs = {}
    for sig_string in EVENT_SIGNATURES:
        event_name = sig_string.split('(')[0]
        sig_hash = web3.keccak(text=sig_string).hex()
        event_sigs[sig_hash] = event_name

    return {
        'wrapper_address': wrapper_address,
        'wrapper_t_address': wrapper_t_address,
        'wrapper_w_address': wrapper_w_address,
        'wrapper_trustless_address': wrapper_trustless_address,
        'event_signatures': event_sigs,
        'trustless_db': trustless_db
    }


def run_historical_sync(db, web3, network_config, alert_func):
    """
    Run historical sync for Homebase.
    Scans for DAO events and syncs proposal/member/vote state.

    Args:
        db: Firestore database client
        web3: Web3 instance
        network_config: Network configuration dict
        alert_func: Function to call for alerts
    """
    doc_name = network_config['firestore_doc_name']
    dao_collection_name = network_config['dao_collection_name']
    trustless_network_collection = network_config.get('trustless_network_collection')

    print("\n--- Starting Homebase Historical Sync ---")

    try:
        # Get network config for fromBlock and lastSyncedBlock from contracts collection
        homebase_contracts_doc = db.collection("contracts").document(doc_name).get()
        if not homebase_contracts_doc.exists:
            print("WARNING: Homebase contracts document not found. Skipping historical sync.")
            return

        homebase_contracts_config = homebase_contracts_doc.to_dict()
        homebase_contracts_ref = db.collection("contracts").document(doc_name)

        REORG_SAFETY_MARGIN = 100
        from_block_config = homebase_contracts_config.get('fromBlock', 0)
        last_synced_block = homebase_contracts_config.get('lastSyncedBlock', 0)
        start_block = max(from_block_config, last_synced_block - REORG_SAFETY_MARGIN)

        if last_synced_block > 0:
            print(f"Checkpoint found: {last_synced_block}. Applying safety margin, starting from {start_block}.")

        latest_block = web3.eth.block_number

        if start_block > latest_block:
            print("Database is already up to date.")
        else:
            print(f"Scanning for Homebase events from block {start_block} to {latest_block}...")

            daos_collection = db.collection(dao_collection_name)

            # ============================================================
            # PHASE 1: Scan wrapper contracts for new DAO creation events
            # ============================================================
            print("\n--- Phase 1: Scanning for new DAOs from wrapper contracts ---")

            # Get all wrapper addresses
            wrapper_address = homebase_contracts_config.get('wrapper') or homebase_contracts_config.get('wrapper_jurisdiction')
            wrapper_t_address = homebase_contracts_config.get('wrapper_t')
            wrapper_w_address = homebase_contracts_config.get('wrapper_w')
            wrapper_trustless_address = homebase_contracts_config.get('wrapper_trustless')

            wrapper_addresses = [addr for addr in [wrapper_address, wrapper_t_address, wrapper_w_address, wrapper_trustless_address] if addr]

            if wrapper_addresses:
                # Build event signature hashes for factory events
                from apps.homebase.config import EVENT_SIGNATURES
                factory_event_names = ['NewDaoCreated', 'DaoWrappedDeploymentInfo', 'SuiteConfigured']
                factory_sig_map = {}  # hash -> (event_name, sig_string)
                for sig_string in EVENT_SIGNATURES:
                    event_name = sig_string.split('(')[0]
                    if event_name in factory_event_names:
                        sig_hash = web3.keccak(text=sig_string).hex()
                        factory_sig_map[sig_hash] = (event_name, sig_string)

                print(f"Scanning {len(wrapper_addresses)} wrapper addresses for factory events...")

                # Get Trustless DB for cross-writing Economy documents
                trustless_db = _init_trustless_firebase()

                # Create wrapper papers
                wrapper_papers = {}
                if wrapper_address:
                    wrapper_papers[wrapper_address] = HomebasePaper(
                        address=wrapper_address, kind="wrapper",
                        daos_collection=daos_collection, db=db, web3=web3
                    )
                if wrapper_t_address:
                    wrapper_papers[wrapper_t_address] = HomebasePaper(
                        address=wrapper_t_address, kind="wrapper_t",
                        daos_collection=daos_collection, db=db, web3=web3
                    )
                if wrapper_w_address:
                    wrapper_papers[wrapper_w_address] = HomebasePaper(
                        address=wrapper_w_address, kind="wrapper_w",
                        daos_collection=daos_collection, db=db, web3=web3
                    )
                if wrapper_trustless_address:
                    wrapper_papers[wrapper_trustless_address] = HomebasePaper(
                        address=wrapper_trustless_address, kind="wrapper_trustless",
                        daos_collection=daos_collection, db=db, web3=web3,
                        trustless_db=trustless_db, trustless_network_collection=trustless_network_collection
                    )

                # Scan for factory events in chunks
                chunk_size = 500
                new_daos_found = 0
                scan_from = start_block

                while scan_from <= latest_block:
                    to_block = min(scan_from + chunk_size - 1, latest_block)
                    try:
                        logs = web3.eth.get_logs({
                            'fromBlock': scan_from,
                            'toBlock': to_block,
                            'address': wrapper_addresses,
                        })

                        # Sort logs by block, tx index, log index to process in order
                        logs = sorted(logs, key=lambda x: (x['blockNumber'], x['transactionIndex'], x['logIndex']))

                        for log in logs:
                            if not log['topics']:
                                continue
                            topic0 = log['topics'][0].hex()
                            if topic0 not in factory_sig_map:
                                continue

                            event_name, _ = factory_sig_map[topic0]
                            log_address = Web3.to_checksum_address(log['address'])

                            if log_address not in wrapper_papers:
                                continue

                            paper = wrapper_papers[log_address]
                            try:
                                if event_name == 'NewDaoCreated':
                                    result = paper.add_dao(log)
                                    if result:
                                        new_daos_found += 1
                                        print(f"  Found new DAO: {result[0][:20]}...")
                                elif event_name == 'DaoWrappedDeploymentInfo':
                                    result = paper.add_dao_wrapped(log)
                                    if result:
                                        new_daos_found += 1
                                        print(f"  Found new wrapped DAO: {result[0][:20]}...")
                                elif event_name == 'SuiteConfigured':
                                    paper.suite_configured(log)
                            except Exception as e:
                                print(f"  Warning: Error processing {event_name} at block {log['blockNumber']}: {e}")

                        scan_from = to_block + 1
                        time.sleep(0.05)  # Rate limiting

                    except Web3RPCError as e:
                        error_message = str(e).lower()
                        if 'block range' in error_message or 'limit' in error_message:
                            chunk_size = max(50, chunk_size // 2)
                            print(f"  Reducing chunk size to {chunk_size}")
                        else:
                            raise e

                print(f"Phase 1 complete: Found {new_daos_found} new DAOs from wrapper events")

            # ============================================================
            # PHASE 2: Sync events for all DAOs (existing + newly discovered)
            # ============================================================
            print("\n--- Phase 2: Syncing events for all DAOs ---")

            # Reload DAOs from Firestore (now includes any newly discovered ones)
            daos_in_db = list(daos_collection.stream())
            print(f"Found {len(daos_in_db)} DAOs in Firestore to sync.")

            # Adaptive chunk size - start conservative and learn what works
            # This is shared across all DAOs so we don't retry failed sizes
            current_chunk_size = 500

            # Scan events for each DAO
            for dao_doc in daos_in_db:
                dao_data = dao_doc.to_dict()
                dao_address = dao_data.get('address')
                token_address = dao_data.get('token')

                if not dao_address or not token_address:
                    print(f"Skipping DAO {dao_doc.id}: missing address or token")
                    continue

                print(f"\nSyncing DAO: {dao_address}")

                # Create paper instances for this DAO
                token_paper = HomebasePaper(
                    address=token_address,
                    kind="token",
                    daos_collection=daos_collection,
                    db=db,
                    web3=web3,
                    dao=dao_address
                )
                dao_paper = HomebasePaper(
                    address=dao_address,
                    kind="dao",
                    token=token_paper,
                    daos_collection=daos_collection,
                    db=db,
                    web3=web3,
                    dao=dao_address
                )

                dao_contract = dao_paper.get_contract()
                token_contract = token_paper.get_contract()

                if not dao_contract or not token_contract:
                    print(f"  WARNING: Contract instances not found for DAO {dao_address}, skipping")
                    continue

                # Collect all events for this DAO
                all_events = []
                from_block_scan = start_block

                print(f"  Scanning events from block {start_block} to {latest_block}...")

                while from_block_scan <= latest_block:
                    to_block = min(from_block_scan + current_chunk_size - 1, latest_block)
                    try:
                        # Scan token events (DelegateChanged)
                        delegate_events = token_contract.events.DelegateChanged.get_logs(
                            from_block=from_block_scan,
                            to_block=to_block
                        )
                        for event in delegate_events:
                            all_events.append(('DelegateChanged', event))

                        # Scan DAO events
                        proposal_created = dao_contract.events.ProposalCreated.get_logs(
                            from_block=from_block_scan,
                            to_block=to_block
                        )
                        for event in proposal_created:
                            all_events.append(('ProposalCreated', event))

                        vote_cast = dao_contract.events.VoteCast.get_logs(
                            from_block=from_block_scan,
                            to_block=to_block
                        )
                        for event in vote_cast:
                            all_events.append(('VoteCast', event))

                        proposal_queued = dao_contract.events.ProposalQueued.get_logs(
                            from_block=from_block_scan,
                            to_block=to_block
                        )
                        for event in proposal_queued:
                            all_events.append(('ProposalQueued', event))

                        proposal_executed = dao_contract.events.ProposalExecuted.get_logs(
                            from_block=from_block_scan,
                            to_block=to_block
                        )
                        for event in proposal_executed:
                            all_events.append(('ProposalExecuted', event))

                        from_block_scan = to_block + 1
                        time.sleep(0.1)  # Rate limiting

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

                # Sort events by block number, then transaction index
                all_events.sort(key=lambda x: (x[1]['blockNumber'], x[1]['transactionIndex'], x[1]['logIndex']))

                print(f"  Found {len(all_events)} events to process")

                # Get existing data from Firestore
                existing_proposals = {}
                existing_votes = {}
                existing_members = {}

                proposals_ref = daos_collection.document(dao_address).collection('proposals')
                for prop_doc in proposals_ref.stream():
                    existing_proposals[prop_doc.id] = prop_doc.to_dict()

                members_ref = daos_collection.document(dao_address).collection('members')
                for member_doc in members_ref.stream():
                    existing_members[member_doc.id] = member_doc.to_dict()

                print(f"  Existing in Firestore: {len(existing_proposals)} proposals, {len(existing_members)} members")

                # Process events chronologically
                events_processed = 0
                for event_type, event in all_events:
                    try:
                        if event_type == 'DelegateChanged':
                            token_paper.delegate(event)
                            events_processed += 1
                        elif event_type == 'ProposalCreated':
                            proposal_id = str(event['args']['proposalId'])
                            if proposal_id not in existing_proposals:
                                dao_paper.propose(event)
                                events_processed += 1
                        elif event_type == 'VoteCast':
                            proposal_id = str(event['args']['proposalId'])
                            voter = Web3.to_checksum_address(event['args']['voter'])
                            # Check if vote already exists
                            vote_ref = proposals_ref.document(proposal_id).collection('votes').document(voter)
                            if not vote_ref.get().exists:
                                dao_paper.vote(event)
                                events_processed += 1
                        elif event_type == 'ProposalQueued':
                            dao_paper.queue(event)
                            events_processed += 1
                        elif event_type == 'ProposalExecuted':
                            dao_paper.execute(event)
                            events_processed += 1
                    except Exception as e:
                        print(f"  - ERROR processing {event_type} event at block {event['blockNumber']}: {e}")

                print(f"  Processed {events_processed} new/updated events for DAO {dao_address}")

            # Update lastSyncedBlock in contracts collection
            homebase_contracts_ref.update({'lastSyncedBlock': latest_block})
            print(f"--- Homebase Historical Sync Complete --- Checkpoint updated to block {latest_block}.")

    except Exception as e:
        error_msg = f"Error during Homebase historical sync: {e}"
        print(f"ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        alert_func(error_msg)


def setup_papers(db, web3, network_config):
    """
    Create Paper objects for Homebase wrapper contracts and all existing DAOs.

    Returns:
        dict with:
            - addresses: list - Addresses to listen to (wrappers + all DAOs + tokens)
            - papers: dict - Paper objects mapped by address
            - daos_collection: Firestore collection reference
    """
    doc_name = network_config['firestore_doc_name']
    dao_collection_name = network_config['dao_collection_name']
    trustless_network_collection = network_config.get('trustless_network_collection')

    # Get wrapper addresses from Firestore
    homebase_doc = db.collection("contracts").document(doc_name).get()
    if not homebase_doc.exists:
        raise Exception(f"Homebase config document '{doc_name}' not found")

    homebase_config = homebase_doc.to_dict()
    # Load all wrapper types
    wrapper_address = homebase_config.get('wrapper') or homebase_config.get('wrapper_jurisdiction')
    wrapper_t_address = homebase_config.get('wrapper_t')  # Transferable non-wrapped
    wrapper_w_address = homebase_config.get('wrapper_w')  # Wrapped ERC20 tokens
    wrapper_trustless_address = homebase_config.get('wrapper_trustless', None)

    daos_collection = db.collection(dao_collection_name)
    homebase_docs = list(daos_collection.stream())
    dao_addresses = [doc.id for doc in homebase_docs]

    # Get Trustless DB (initialized during initialize())
    trustless_db = _trustless_db

    # Create Paper objects
    papers = {}
    addresses = [wrapper_address]
    if wrapper_t_address:
        addresses.append(wrapper_t_address)
    if wrapper_w_address:
        addresses.append(wrapper_w_address)
    if wrapper_trustless_address:
        addresses.append(wrapper_trustless_address)

    # Wrapper papers
    papers[wrapper_address] = HomebasePaper(
        address=wrapper_address,
        kind="wrapper",
        daos_collection=daos_collection,
        db=db,
        web3=web3
    )
    if wrapper_t_address:
        papers[wrapper_t_address] = HomebasePaper(
            address=wrapper_t_address,
            kind="wrapper_t",
            daos_collection=daos_collection,
            db=db,
            web3=web3
        )
    if wrapper_w_address:
        papers[wrapper_w_address] = HomebasePaper(
            address=wrapper_w_address,
            kind="wrapper_w",
            daos_collection=daos_collection,
            db=db,
            web3=web3
        )
    if wrapper_trustless_address:
        # TrustlessFactory wrapper gets additional Trustless DB for cross-writing
        papers[wrapper_trustless_address] = HomebasePaper(
            address=wrapper_trustless_address,
            kind="wrapper_trustless",
            daos_collection=daos_collection,
            db=db,
            web3=web3,
            trustless_db=trustless_db,
            trustless_network_collection=trustless_network_collection
        )

    # DAO, Token, and Registry papers
    addresses.extend(dao_addresses)
    for doc in homebase_docs:
        obj = doc.to_dict()
        token_address, dao_address = obj.get('token'), obj.get('address')
        registry_address = obj.get('registryAddress')  # For Economy DAOs
        if token_address and dao_address:
            addresses.append(token_address)
            token_paper = HomebasePaper(
                address=token_address,
                kind="token",
                daos_collection=daos_collection,
                db=db,
                web3=web3,
                dao=dao_address
            )
            dao_paper = HomebasePaper(
                address=dao_address,
                kind="dao",
                token=token_paper,
                daos_collection=daos_collection,
                db=db,
                web3=web3,
                dao=dao_address
            )
            papers[token_address] = token_paper
            papers[dao_address] = dao_paper
            
            # Add registry paper for Economy DAOs (those with an economy address)
            if registry_address and obj.get('economy'):
                addresses.append(registry_address)
                registry_paper = HomebasePaper(
                    address=registry_address,
                    kind="registry",
                    daos_collection=daos_collection,
                    db=db,
                    web3=web3,
                    dao=dao_address
                )
                papers[registry_address] = registry_paper
                print(f"Added registry paper for Economy DAO {dao_address[:10]}... at {registry_address}")

    return {
        'addresses': addresses,
        'papers': papers,
        'daos_collection': daos_collection
    }

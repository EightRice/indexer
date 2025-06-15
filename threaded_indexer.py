"""Threaded indexer for processing blockchain events."""

import argparse
import logging
import queue
import threading
import time
from firebase_admin import credentials, firestore, initialize_app
from web3 import Web3

from apps.homebase.paper import Paper

def initialize_environment():
    """Initialize Firebase and Web3 environments.
    Returns
    -------
    tuple
        Initialized ``web3`` instance, dictionary of ``Paper`` objects, DAO
        collection handle, Firestore database handle, mapping of event
        signatures to names, and the list of contract addresses to listen to.
    """

    cred = credentials.Certificate("homebase.json")
    initialize_app(cred)
    db = firestore.client()

    networks = db.collection("contracts")
    ceva = networks.document("Etherlink-Testnet").get()
    rpc = "https://node.ghostnet.etherlink.com"
    wrapper_address = ceva.to_dict()['wrapper']
    wrapper_w_address = ceva.to_dict()['wrapper_w']

    web3 = Web3(Web3.HTTPProvider(rpc))
    if web3.is_connected():
        logging.info("node connected")
    else:
        logging.error("node connection failed!")

    papers = {}
    daos_collection = db.collection('idaosEtherlink-Testnet')
    docs = list(daos_collection.stream())

    for doc in docs:
        obj = doc.to_dict()
        try:
            p = Paper(address=obj['token'], kind="token",
                      daos_collection=daos_collection, db=db, web3=web3, dao=doc.id)
            dao = Paper(address=obj['address'], kind="dao", token=p,
                        daos_collection=daos_collection, db=db, web3=web3, dao=doc.id)
        except Exception as e:
            logging.warning("One DAO contract can't parse correctly for %s: %s", doc.id, e)
            continue
        papers[obj['token']] = p
        papers[obj['address']] = dao

    event_text_wrapped_dao = "DaoWrappedDeploymentInfo(address,address,address,string,string,string,uint8)"
    keccak_hash_wrapped_dao = web3.keccak(text=event_text_wrapped_dao).hex()

    event_signatures = {
        web3.keccak(text="NewDaoCreated(address,address,address[],uint256[],string,string,string,uint256,address,string[],string[])").hex(): "NewDaoCreated",
        keccak_hash_wrapped_dao: "DaoWrappedDeploymentInfo",
        "0x3134e8a2e6d97e929a7e54011ea5485d7d196dd5f0ba4d4ef95803e8e3fc257f": "DelegateChanged",
        "0x7d84a6263ae0d98d3329bd7b46bb4e8d6f98cd35a7adb45c274c8b7fd5ebd5e0": "ProposalCreated",
        "0x9a2e42fd6722813d69113e7d0079d3d940171428df7373df9c7f7617cfda2892": "ProposalQueued",
        "0x712ae1383f79ac853f8d882153778e0260ef8f03b504e2866e0593e04d2b291f": "ProposalExecuted",
        "0xb8e138887d0aa13bab447e82de9d5c1777041ecd21ca36ba824ff1e6c07ddda4": "VoteCast",
    }

    papers[wrapper_address] = Paper(address=wrapper_address, kind="wrapper",
                                    daos_collection=daos_collection, db=db, web3=web3)
    papers[wrapper_w_address] = Paper(address=wrapper_w_address, kind="wrapper_w",
                                      daos_collection=daos_collection, db=db, web3=web3)

    dao_addresses = [doc.id for doc in docs]
    listening_to_addresses = [wrapper_address, wrapper_w_address] + list(dao_addresses)
    listening_to_addresses = [addr for addr in listening_to_addresses if addr is not None]
    known_token_addresses = [paper_obj.address for paper_obj in papers.values()
                             if paper_obj.kind == "token" and paper_obj.address]
    listening_to_addresses.extend(known_token_addresses)
    listening_to_addresses = list(set(listening_to_addresses))

    logging.info(
        "Listening for %d events on %d contracts: %s",
        len(event_signatures),
        len(listening_to_addresses),
        listening_to_addresses,
    )

    return web3, papers, daos_collection, db, event_signatures, listening_to_addresses


def event_listener(
    event_queue,
    web3,
    event_signatures,
    listening_to_addresses,
    processed_tx,
    lock,
    stop_event,
    poll_interval=5,
):
    """Poll blockchain logs and enqueue relevant events."""

    while not stop_event.is_set():
        try:
            latest = web3.eth.block_number
            first = latest - 13 if latest > 13 else 0
            with lock:
                addresses = list(listening_to_addresses)
            logs = web3.eth.get_logs({
                "fromBlock": first,
                "toBlock": latest,
                "address": addresses,
            })
            for log_entry in logs:
                tx_hash = log_entry["transactionHash"].hex()
                with lock:
                    if tx_hash in processed_tx:
                        continue
                    processed_tx.add(tx_hash)
                if not log_entry["topics"]:
                    continue
                event_signature_from_log = log_entry["topics"][0].hex()
                event_name = event_signatures.get(event_signature_from_log)
                if event_name:
                    event_queue.put((log_entry, event_name))
        except Exception as exc:
            logging.exception("Listener error: %s", exc)
        stop_event.wait(poll_interval)


def worker(event_queue, papers, listening_to_addresses, daos_collection, db, web3, lock, stop_event):
    """Process events pulled from the queue."""

    while not stop_event.is_set():
        try:
            log_entry, event_name = event_queue.get(timeout=1)
        except queue.Empty:
            continue
        contract_address = log_entry["address"]
        with lock:
            paper = papers.get(contract_address)
        if not paper:
            logging.warning("Paper object not found for contract address: %s", contract_address)
            event_queue.task_done()
            continue
        try:
            new_contract_addresses = paper.handle_event(log_entry, func=event_name)
        except Exception as exc:
            logging.exception("Error processing event %s for %s: %s", event_name, contract_address, exc)
            event_queue.task_done()
            continue

        if new_contract_addresses:
            dao_address_new, token_address_new = new_contract_addresses
            with lock:
                if dao_address_new and dao_address_new not in listening_to_addresses:
                    listening_to_addresses.append(dao_address_new)
                if token_address_new and token_address_new not in listening_to_addresses:
                    listening_to_addresses.append(token_address_new)
                if token_address_new and token_address_new not in papers:
                    p_new_token = Paper(address=token_address_new, kind="token",
                                         daos_collection=daos_collection, db=db, dao=dao_address_new, web3=web3)
                    papers[token_address_new] = p_new_token
                else:
                    p_new_token = papers.get(token_address_new)
                if dao_address_new and dao_address_new not in papers:
                    papers[dao_address_new] = Paper(token=p_new_token, address=dao_address_new,
                                                   kind="dao", daos_collection=daos_collection, db=db,
                                                   dao=dao_address_new, web3=web3)
        event_queue.task_done()

    logging.info("Worker exiting")


def main(worker_count=4, poll_interval=5):
    """Entry point to start the threaded indexer."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    web3, papers, daos_collection, db, event_signatures, listening_to_addresses = initialize_environment()
    processed_tx = set()
    lock = threading.Lock()
    stop_event = threading.Event()
    event_queue = queue.Queue()

    threads = []
    for _ in range(worker_count):
        t = threading.Thread(
            target=worker,
            args=(event_queue, papers, listening_to_addresses, daos_collection, db, web3, lock, stop_event),
            daemon=True,
        )
        t.start()
        threads.append(t)

    listener = threading.Thread(
        target=event_listener,
        args=(
            event_queue,
            web3,
            event_signatures,
            listening_to_addresses,
            processed_tx,
            lock,
            stop_event,
        ),
        kwargs={"poll_interval": poll_interval},
        daemon=True,
    )
    listener.start()

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down.")
        stop_event.set()
        listener.join()
        for t in threads:
            t.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the threaded indexer")
    parser.add_argument("--workers", type=int, default=4, help="Number of worker threads")
    parser.add_argument("--poll", type=int, default=5, help="Polling interval in seconds")
    args = parser.parse_args()

    main(worker_count=args.workers, poll_interval=args.poll)

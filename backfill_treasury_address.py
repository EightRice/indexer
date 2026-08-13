#!/usr/bin/env python3
"""
Backfill `treasuryAddress` for DAO documents that were indexed before the
treasury fix (see apps/homebase/paper.py::fetch_treasury_address).

Background
----------
The factory events `NewDaoCreated` and `DaoWrappedDeploymentInfo` do not carry
the timelock/treasury address, so DAOs indexed before the fix were written to
Firestore with `treasuryAddress: null`. HomebaseDAO extends OpenZeppelin's
GovernorTimelockControl, which exposes a public `timelock()` getter returning
the TimelockController that holds the DAO's funds. This script reads that
getter per DAO and repairs the document.

Usage
-----
Dry run (default -- reads chain + Firestore, writes nothing):

    python backfill_treasury_address.py
    python backfill_treasury_address.py --network shadownet

Apply the updates:

    python backfill_treasury_address.py --network shadownet --apply

Run with no --network to cover every configured network.

Safety
------
- Dry run is the default; `--apply` is required to write.
- Only documents whose `treasuryAddress` is missing/null/empty are touched.
  Documents that already have a treasury are never overwritten.
- Only the `treasuryAddress` field is written; no other field is modified.
"""

import argparse
import re
import sys

import firebase_admin
from firebase_admin import credentials, firestore
from web3 import Web3

from apps.homebase.abis import daoAbiGlobal
from apps.homebase.config import FIREBASE_CREDENTIALS, NETWORKS

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# RPC endpoint per network key (matches app.py).
RPC_URLS = {
    "mainnet": "https://node.mainnet.etherlink.com",
    "testnet": "https://node.ghostnet.etherlink.com",
    "shadownet": "https://node.shadownet.etherlink.com",
    "localhost": "http://127.0.0.1:8545",
    "base-sepolia": "https://sepolia.base.org",
}


def read_timelock(web3, dao_address, abi):
    """Return the checksummed timelock/treasury address for a DAO, or None."""
    try:
        contract = web3.eth.contract(
            address=Web3.to_checksum_address(dao_address), abi=abi
        )
        timelock_address = contract.functions.timelock().call()
    except Exception as e:
        print(f"    ERROR reading timelock(): {e}")
        return None

    if not timelock_address or timelock_address == ZERO_ADDRESS:
        print("    timelock() returned the zero address")
        return None
    return Web3.to_checksum_address(timelock_address)


def backfill_network(db, network, apply_changes, abi):
    config = NETWORKS.get(network)
    rpc_url = RPC_URLS.get(network)
    if not config or not rpc_url:
        print(f"Skipping unknown network '{network}'")
        return 0, 0

    collection_name = config["dao_collection_name"]
    print(f"\n=== {network} ({collection_name}) ===")

    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        print(f"  Could not connect to RPC {rpc_url}; skipping.")
        return 0, 0

    fixed = 0
    failed = 0
    for doc in db.collection(collection_name).stream():
        data = doc.to_dict() or {}
        existing = data.get("treasuryAddress")
        if existing:
            continue  # Already populated -- never overwrite.

        dao_address = data.get("address") or doc.id
        name = data.get("name", "<unnamed>")
        print(f"  {name} ({dao_address}): treasuryAddress is null")

        treasury = read_timelock(web3, dao_address, abi)
        if not treasury:
            failed += 1
            continue

        if apply_changes:
            try:
                db.collection(collection_name).document(doc.id).update(
                    {"treasuryAddress": treasury}
                )
                print(f"    UPDATED -> {treasury}")
                fixed += 1
            except Exception as e:
                print(f"    ERROR writing Firestore: {e}")
                failed += 1
        else:
            print(f"    DRY RUN would set -> {treasury}")
            fixed += 1

    return fixed, failed


def main():
    parser = argparse.ArgumentParser(
        description="Backfill null treasuryAddress fields on DAO documents."
    )
    parser.add_argument(
        "--network",
        choices=sorted(NETWORKS.keys()),
        help="Network to backfill. Omit to process all configured networks.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to Firestore. Without this flag the script is a dry run.",
    )
    args = parser.parse_args()

    if not firebase_admin._apps:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)
        firebase_admin.initialize_app(cred)
    db = firestore.client()

    abi = re.sub(r"\n+", " ", daoAbiGlobal).strip()
    networks = [args.network] if args.network else sorted(NETWORKS.keys())

    if not args.apply:
        print("DRY RUN -- no writes will be made. Re-run with --apply to persist.")

    total_fixed = 0
    total_failed = 0
    for network in networks:
        fixed, failed = backfill_network(db, network, args.apply, abi)
        total_fixed += fixed
        total_failed += failed

    verb = "Updated" if args.apply else "Would update"
    print(f"\n{verb} {total_fixed} DAO document(s). {total_failed} could not be resolved.")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())

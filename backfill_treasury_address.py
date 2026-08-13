#!/usr/bin/env python3
"""
Set `treasuryAddress` = `registryAddress` on DAO documents.

Background
----------
In Homebase EVM DAOs the Registry contract IS the treasury: it holds the
DAO's funds (with spend caps and earmarking); the OZ TimelockController is
only the execution-delay mechanism. The app's treasury UI reads
`registryAddress` everywhere.

An earlier version of this script wrongly populated `treasuryAddress` with
the timelock address (OpenZeppelin convention, not Homebase's design). This
version corrects that: it sets `treasuryAddress` to the document's own
`registryAddress`, overwriting timelock values and filling nulls alike.
No chain reads are needed; the correct value is already in each document.

Usage
-----
Dry run (default -- writes nothing):

    python backfill_treasury_address.py
    python backfill_treasury_address.py --network shadownet

Apply the updates:

    python backfill_treasury_address.py --network shadownet --apply

Safety
------
- Dry run is the default; `--apply` is required to write.
- Only documents where treasuryAddress != registryAddress are touched.
- Only the `treasuryAddress` field is written; no other field is modified.
- Documents with no registryAddress are reported and skipped.
"""

import argparse
import sys

import firebase_admin
from firebase_admin import credentials, firestore

from apps.homebase.config import FIREBASE_CREDENTIALS, NETWORKS


def correct_network(db, network, apply_changes):
    config = NETWORKS.get(network)
    if not config:
        print(f"Skipping unknown network '{network}'")
        return 0, 0

    collection_name = config["dao_collection_name"]
    print(f"\n=== {network} ({collection_name}) ===")

    fixed = 0
    skipped = 0
    for doc in db.collection(collection_name).stream():
        data = doc.to_dict() or {}
        registry = data.get("registryAddress")
        treasury = data.get("treasuryAddress")
        name = data.get("name", "<unnamed>")
        dao_address = data.get("address") or doc.id

        if not registry:
            print(f"  {name} ({dao_address}): no registryAddress; SKIPPED")
            skipped += 1
            continue
        if treasury == registry:
            continue  # Already correct.

        print(f"  {name} ({dao_address}): treasuryAddress {treasury!r} -> {registry}")
        if apply_changes:
            try:
                db.collection(collection_name).document(doc.id).update(
                    {"treasuryAddress": registry}
                )
                print("    UPDATED")
                fixed += 1
            except Exception as e:
                print(f"    ERROR writing Firestore: {e}")
                skipped += 1
        else:
            fixed += 1

    return fixed, skipped


def main():
    parser = argparse.ArgumentParser(
        description="Set treasuryAddress = registryAddress on DAO documents."
    )
    parser.add_argument(
        "--network",
        choices=sorted(NETWORKS.keys()),
        help="Network to correct. Omit to process all configured networks.",
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

    networks = [args.network] if args.network else sorted(NETWORKS.keys())

    if not args.apply:
        print("DRY RUN -- no writes will be made. Re-run with --apply to persist.")

    total_fixed = 0
    total_skipped = 0
    for network in networks:
        fixed, skipped = correct_network(db, network, args.apply)
        total_fixed += fixed
        total_skipped += skipped

    verb = "Updated" if args.apply else "Would update"
    print(f"\n{verb} {total_fixed} DAO document(s). {total_skipped} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

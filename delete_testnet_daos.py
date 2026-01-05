#!/usr/bin/env python3
"""
Script to delete specific DAOs from idaosEtherlink-Testnet collection.
This removes the DAO documents and their subcollections (proposals, members, votes).
"""

import firebase_admin
from firebase_admin import credentials, firestore

# DAOs to delete from Etherlink-Testnet
DAOS_TO_DELETE = [
    "0x0cEe9dA3EcB2C466C0a7eEaA227024Aa331Ab1A2",
    "0x14176d6438aE47D9929285f32201E3Cfe699b549",
    "0x178AFFc08550ad39D8551947365F8F91b1413117",
    "0x156C097e1a527524f0593c254324eB2fCB2A7Ff2",
    "0x1214b448051AFcA439322120e1e9C1F917B1F8Ff",
    "0x189e5F05aEb030AE516D5fe20fc1cE389b7B5cdF",
    "0x1b0274f664c299dbDAE356d5B8FE5385db5B54b8",
    "0x4429a38f4F58D46DD436D57C6C719a581e5433e5",
    "0x42af9dfcBb55C30Bbc9927397dEa5fBE92A5F004",
    "0x32A9c4fee8f4Acbf7055f27E44a0189e2ea4d864",
    "0x450b419148Ed29991b56cF5Ba498ac007eEd3d80",
    "0x5E1007A0f3fb3F0267c25bB7Aa2251A63fB7DEBC",
    "0x61b04214ed287e419aAd13A1A2D192A267F87bBa",
    "0x645D2d673d7F17F6Bbdc3195bda6bCE582ab8984",
    "0x6983bd593734B963fB767f43296Fcd81f9811510",
    "0x66d42E191a10c249ABF8348aA0c7452B09D650c1",
    "0x6466D885425c132bb2952Cd4Adfd144a745ac368",
    "0x722ebdE8D6E2B232A56e616a67C61878089A001C",
    "0x75825da0f41f440E7e159b867272Fc02648DaE70",
    "0x77D4552094792CC5bbE076e08F4249DE0Ee71C48",
    "0x7827d29544C185e2d95F9834a9c0aa44B8fd8E30",
    "0x7f924376Bbd9dB9B5354E826E690A322603216A4",
    "0x9B29f725D4acc17C64d18984e2eFd1295C081dF2",
    "0x9F1E2cd3b7d9Ad5354eCddDB411bE10c63a97aCE",
    "0x9f1b5AbcADA80C5F72B2615e3cC96328018BB0c7",
    "0xA3251B1fC8B8E1Cbea1A3544A58970812EFD007f",
    "0xB19d598FFBd97A3a70B7c627adc1f8FBeb47f909",
    "0xC105BB7efC69ABc3f94014Da1bcebc7eFBEeB092",
    "0xC2fd91104F7D258C63EabAE95ED3b8ceb6901EC4",
    "0xC89E9d1DEa1aBd342028955D6f9B2d12a06DE794",
    "0xDe798859cFcc8690362615E9C1aB41BFE6852751",
    "0xE2aa9F59BC9d865145c5BD87bE98c372Ae253747",
    "0xD30041126290B59F434F583DB2053a4e1ce37dd6",
    "0xCaDc31A896FB344Cff56CAD485aBCB33EC2f1176",
    "0xFD2be6d41c25F801E01fC0C1B2E74F4225C380BB",
    "0xFDFd5a6B1c555646eC63E3BDC305114ecED82D5C",
    "0xFe04F2C73BB706e3086559dfD15CeCEa5240068A",
    "0xeeD8DDc550Fa41a47f95f5A7F271E45BA3aD3f67",
    "0xf99641B2b5986067283A74c128aEc1740eC228C1",
    "0xeD69FddffFE2be67040Ad9E3b559417b0d92556a",
    "0xe197D7b3BE23DaE17B12204e8cf8753137c0df88",
    "0xe70da5e76146F430C3727fAd0d8d23135497f5bc",
    "0xe18F5F27BDcDC343E93E93919FA91CE509428096",
    "0xEd95020d4F37730442C13F35bcE84D7F69fC7FBF",
    "0xD1A353f4Ce565F2dfBbDA94BBFa457403DdF9359",
]

COLLECTION_NAME = "idaosEtherlink-Testnet"


def delete_collection(coll_ref, batch_size=100):
    """Recursively delete all documents in a collection."""
    docs = coll_ref.limit(batch_size).stream()
    deleted = 0

    for doc in docs:
        # First delete any subcollections
        for subcoll in doc.reference.collections():
            delete_collection(subcoll, batch_size)

        doc.reference.delete()
        deleted += 1

    if deleted >= batch_size:
        # There might be more documents, recurse
        return delete_collection(coll_ref, batch_size)

    return deleted


def main():
    # Initialize Firebase
    cred = credentials.Certificate("homebase.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    collection = db.collection(COLLECTION_NAME)

    # Remove duplicates (case-insensitive) but preserve original case
    seen = set()
    unique_daos = []
    for addr in DAOS_TO_DELETE:
        if addr.lower() not in seen:
            seen.add(addr.lower())
            unique_daos.append(addr)
    print(f"Will delete {len(unique_daos)} unique DAOs from {COLLECTION_NAME}")

    # First, let's see what exists
    existing_count = 0
    for dao_addr in unique_daos:
        doc = collection.document(dao_addr).get()
        if doc.exists:
            existing_count += 1

    print(f"Found {existing_count} of these DAOs in the database")

    confirm = input("\nType 'DELETE' to confirm deletion: ")
    if confirm != "DELETE":
        print("Aborted.")
        return

    deleted_count = 0
    skipped_count = 0

    for dao_addr in unique_daos:
        doc_ref = collection.document(dao_addr)
        doc = doc_ref.get()

        if not doc.exists:
            print(f"  SKIP: {dao_addr} (not found)")
            skipped_count += 1
            continue

        dao_data = doc.to_dict()
        token_addr = dao_data.get('token', 'N/A')
        name = dao_data.get('name', 'Unknown')

        # IMPORTANT: Delete subcollections BEFORE the parent document
        # Firestore does NOT automatically delete subcollections - they become orphaned

        # 1. Delete votes under each proposal (deepest level first)
        proposals_ref = doc_ref.collection('proposals')
        proposals_deleted = 0
        votes_deleted = 0
        for prop_doc in proposals_ref.stream():
            votes_ref = prop_doc.reference.collection('votes')
            for vote_doc in votes_ref.stream():
                vote_doc.reference.delete()
                votes_deleted += 1
            prop_doc.reference.delete()
            proposals_deleted += 1

        # 2. Delete members
        members_ref = doc_ref.collection('members')
        members_deleted = 0
        for member_doc in members_ref.stream():
            member_doc.reference.delete()
            members_deleted += 1

        # 3. Finally delete the DAO document itself
        doc_ref.delete()
        deleted_count += 1
        print(f"  DELETED: {dao_addr[:16]}... ({name})")
        print(f"           -> {proposals_deleted} proposals, {votes_deleted} votes, {members_deleted} members")

    print(f"\n--- Summary ---")
    print(f"Deleted: {deleted_count}")
    print(f"Skipped (not found): {skipped_count}")
    print(f"Total processed: {deleted_count + skipped_count}")


if __name__ == "__main__":
    main()

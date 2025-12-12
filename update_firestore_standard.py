import firebase_admin
from firebase_admin import credentials, firestore

# Use the same credentials as the indexer
if not firebase_admin._apps:
    cred = credentials.Certificate('homebase.json')
    homebase_app = firebase_admin.initialize_app(cred, name='homebaseApp')
    print("[OK] Initialized Firebase with homebase.json credentials")

db = firestore.client(app=homebase_app)

NETWORK = "Etherlink-Testnet"  # Must match indexer's homebase_fs_doc_name
STANDARD_FACTORY_ADDRESS = "0xD5c69deaA484acAE65bA1Fb4602bDF641c804745"  # StandardFactory wrapper

print(f"\n[INFO] Updating Firestore for network: {NETWORK}")
print(f"[INFO] StandardFactory address: {STANDARD_FACTORY_ADDRESS}")

# Update contracts document with standard factory wrapper
contracts_ref = db.collection('contracts').document(NETWORK)
contracts_ref.set({
    'wrapper_jurisdiction': STANDARD_FACTORY_ADDRESS,  # Using jurisdiction field for standard factory
    'wrapper_w': '0x0000000000000000000000000000000000000000',  # No wrapped token wrapper yet
}, merge=True)

print(f"[SUCCESS] Updated contracts/{NETWORK} with wrapper_jurisdiction = {STANDARD_FACTORY_ADDRESS}")

# Verify the update
updated_doc = contracts_ref.get()
if updated_doc.exists:
    data = updated_doc.to_dict()
    print(f"\n[VERIFY] Current Firestore state for contracts/{NETWORK}:")
    print(f"  wrapper_jurisdiction: {data.get('wrapper_jurisdiction', 'NOT SET')}")
    print(f"  wrapper_w: {data.get('wrapper_w', 'NOT SET')}")
else:
    print(f"\n[WARN] Document contracts/{NETWORK} was not found after update")

# Check networks document
networks_ref = db.collection('networks').document(NETWORK)
networks_doc = networks_ref.get()
if networks_doc.exists:
    print(f"\n[INFO] Networks/{NETWORK} configuration:")
    net_data = networks_doc.to_dict()
    print(f"  rpc: {net_data.get('rpc', 'NOT SET')}")
    print(f"  fromBlock: {net_data.get('fromBlock', 'NOT SET')}")
    print(f"  lastSyncedBlock: {net_data.get('lastSyncedBlock', 'NOT SET')}")
else:
    print(f"\n[WARN] networks/{NETWORK} document not found")
    print("[INFO] Creating networks document...")
    networks_ref.set({
        'rpc': 'https://node.ghostnet.etherlink.com',
        'fromBlock': 0,
        'lastSyncedBlock': 0,
    })
    print(f"[SUCCESS] Created networks/{NETWORK} document")

print("\n[DONE] Firestore update complete!")
print("\nNext steps:")
print("1. Restart the indexer to pick up the new wrapper address")
print("2. Deploy a test DAO through the StandardFactory")
print("3. Verify the indexer captures the NewDaoCreated event")

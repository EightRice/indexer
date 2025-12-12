"""
Update Firestore with Etherlink Testnet contract addresses
Uses homebase.json credentials (same as indexer)
"""
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase using homebase.json (same as indexer)
if not firebase_admin._apps:
    cred = credentials.Certificate('homebase.json')
    homebase_app = firebase_admin.initialize_app(cred, name='homebaseApp')
    print("[OK] Initialized Firebase with homebase.json credentials (same as indexer)")

db = firestore.client(app=homebase_app)

# Deployed contract addresses on Etherlink Testnet
NETWORK = "Etherlink-Testnet"  # Must match indexer's homebase_fs_doc_name
WRAPPER_ADDRESS = "0x488C638D919a5A0AbfD24B7010fcB3E391341196"  # Registry
WRAPPER_W_ADDRESS = "0x0000000000000000000000000000000000000000"  # Placeholder

print(f"\nUpdating Firestore for network: {NETWORK}")
print(f"Wrapper (Registry) address: {WRAPPER_ADDRESS}")

# Update contracts document
contracts_ref = db.collection('contracts').document(NETWORK)
contracts_ref.set({
    'wrapper_jurisdiction': WRAPPER_ADDRESS,
    'wrapper_w': WRAPPER_W_ADDRESS,
}, merge=True)
print(f"[OK] Updated contracts/{NETWORK}")

# Update networks document
networks_ref = db.collection('networks').document(NETWORK)
networks_ref.set({
    'rpc': 'https://node.ghostnet.etherlink.com',
    'fromBlock': 0,
    'lastSyncedBlock': 0,
}, merge=True)
print(f"[OK] Updated networks/{NETWORK}")

print(f"\n[SUCCESS] Firestore configuration complete for {NETWORK}!")
print(f"\nYou can now run the indexer with:")
print(f"   python app.py {NETWORK} homebase")

import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase with homebase credentials
cred = credentials.Certificate('homebase.json')
homebase_app = firebase_admin.initialize_app(cred, name='homebaseApp')
db = firestore.client(app=homebase_app)

NETWORK = "Etherlink-Testnet"
TRUSTLESS_FACTORY_ADDRESS = "0xFB3dE5d465264557464950a9E824eBf1acc33394"

print(f"Updating Firestore with TrustlessFactory address for {NETWORK}...")

contracts_ref = db.collection('contracts').document(NETWORK)
contracts_ref.set({
    'wrapper_trustless': TRUSTLESS_FACTORY_ADDRESS,
}, merge=True)

print(f"TrustlessFactory address updated: {TRUSTLESS_FACTORY_ADDRESS}")

# Verify the update
doc = contracts_ref.get()
if doc.exists:
    data = doc.to_dict()
    print("\nCurrent Firestore configuration:")
    print(f"  wrapper_jurisdiction: {data.get('wrapper_jurisdiction', 'NOT SET')}")
    print(f"  wrapper_w: {data.get('wrapper_w', 'NOT SET')}")
    print(f"  wrapper_trustless: {data.get('wrapper_trustless', 'NOT SET')}")

firebase_admin.delete_app(homebase_app)
print("\nDone!")

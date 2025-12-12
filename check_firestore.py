"""
Check Firestore documents - using same credentials as indexer
"""
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase using homebase.json (same as indexer)
if not firebase_admin._apps:
    cred = credentials.Certificate('homebase.json')
    homebase_app = firebase_admin.initialize_app(cred, name='homebaseApp')
    print("Using homebase.json credentials (same as indexer)")

db = firestore.client(app=homebase_app)

# Check contracts document
print("\n=== Checking contracts/Etherlink-Testnet ===")
contracts_doc = db.collection('contracts').document('Etherlink-Testnet').get()
if contracts_doc.exists:
    print(f"Document exists:")
    for key, value in contracts_doc.to_dict().items():
        print(f"  {key}: {value}")
else:
    print("Document does NOT exist!")

# Check networks document
print("\n=== Checking networks/Etherlink-Testnet ===")
networks_doc = db.collection('networks').document('Etherlink-Testnet').get()
if networks_doc.exists:
    print(f"Document exists:")
    for key, value in networks_doc.to_dict().items():
        print(f"  {key}: {value}")
else:
    print("Document does NOT exist!")

# Check all documents in contracts collection
print("\n=== All documents in contracts collection ===")
contracts_ref = db.collection('contracts')
for doc in contracts_ref.stream():
    print(f"  {doc.id}")

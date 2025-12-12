# apps/afterme/config.py
# AfterMe app configuration

APP_NAME = "afterme"

# Firebase credentials file path (relative to indexer root)
FIREBASE_CREDENTIALS = "afterme.json"

# Network-specific configurations
NETWORKS = {
    "mainnet": {
        "firestore_doc_name": "Etherlink",
        "wills_collection_name": "willsEtherlink",
    },
    "testnet": {
        "firestore_doc_name": "Etherlink-Testnet",
        "wills_collection_name": "willsEtherlink-Testnet",
    },
    "localhost": {
        "firestore_doc_name": "Localhost",
        "wills_collection_name": "willsLocalhost",
    },
}

# Event signatures that this app listens for
EVENT_SIGNATURES = [
    "WillCreated(address,address,bool)",
    "WillCleared(address,address)",
    "Ping(uint256)",
    "Executed(address,uint256,address)",
    "Cancelled()",
    "WillConfigured(address)",
    "WillEmptied(address)",
]

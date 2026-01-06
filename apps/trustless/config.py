# apps/trustless/config.py

APP_NAME = "trustless"

# Firebase credentials file path (relative to indexer root)
FIREBASE_CREDENTIALS = "secrets/trustless.json"

# Network-specific configurations
# Note: lastSyncedBlock checkpoint is always stored in contracts/{firestore_doc_name}
NETWORKS = {
    "mainnet": {
        "firestore_doc_name": "Etherlink",
        "network_collection": "Etherlink",
    },
    "testnet": {
        "firestore_doc_name": "Etherlink-Testnet",
        "network_collection": "Etherlink-Testnet",
    },
    "shadownet": {
        "firestore_doc_name": "Etherlink-Shadownet",
        "network_collection": "Etherlink-Shadownet",
    },
    "localhost": {
        "firestore_doc_name": "Localhost",
        "network_collection": "Localhost",
    },
    "base-sepolia": {
        "firestore_doc_name": "Base-Sepolia",
        "network_collection": "Base-Sepolia",
    },
}

# Economy contract event signatures
ECONOMY_EVENT_SIGNATURES = [
    "NewProject(address,string,address,address,string,string,string,address)",
]

# Project contract event signatures (both NativeProject and ERC20Project)
PROJECT_EVENT_SIGNATURES = [
    "SetParties(address,address,string)",
    "SendFunds(address,uint256,uint256)",
    "ImmediateFundsReleased(address,uint256)",
    "ContractSigned(address)",
    "ProjectDisputed(address)",
    "ProjectClosed(address)",
    "ArbitrationDecision(address,uint256,string)",
    "ArbitrationAppealed(address,uint256)",
    "ArbitrationFinalized(address)",
    "DaoOverruled(address,uint256,string)",
    "ContractorPaid(address,uint256)",
    "ContributorWithdrawn(address,uint256)",
    "AuthorPaid(address,uint256)",
    "VetoedByDao(address)",
    "BackerVoteCast(address,uint256,bool)",
]

# All event signatures combined
EVENT_SIGNATURES = ECONOMY_EVENT_SIGNATURES + PROJECT_EVENT_SIGNATURES

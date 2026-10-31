# apps/trustless/config.py

APP_NAME = "trustless"

# Projects are written to the homebase Firestore, under the DAO their Economy belongs to:
# {dao_collection_name}/{dao}/projects/{project}, next to the DAO's proposals and debates.
FIREBASE_CREDENTIALS = "homebase.json"

# The checkpoint lives in the homebase contracts/{firestore_doc_name} doc, in its own field: the
# homebase app's historical sync advances lastSyncedBlock on its own.
CHECKPOINT_FIELD = "projectsLastSyncedBlock"

# Network-specific configurations
NETWORKS = {
    "mainnet": {
        "firestore_doc_name": "Etherlink",
        "dao_collection_name": "idaosEtherlink",
        "checkpoint_field": CHECKPOINT_FIELD,
    },
    "testnet": {
        "firestore_doc_name": "Etherlink-Testnet",
        "dao_collection_name": "idaosEtherlink-Testnet",
        "checkpoint_field": CHECKPOINT_FIELD,
    },
    "shadownet": {
        "firestore_doc_name": "Etherlink-Shadownet",
        "dao_collection_name": "idaosEtherlink-Shadownet",
        "checkpoint_field": CHECKPOINT_FIELD,
    },
    "localhost": {
        "firestore_doc_name": "Localhost",
        "dao_collection_name": "idaosLocalhost",
        "checkpoint_field": CHECKPOINT_FIELD,
    },
    "base-sepolia": {
        "firestore_doc_name": "Base-Sepolia",
        "dao_collection_name": "idaosBase-Sepolia",
        "checkpoint_field": CHECKPOINT_FIELD,
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
    "ArbitrationFinalized(address)",
    "DaoOverruled(address,uint256,string)",
    "ContractorPaid(address,uint256)",
    "ContributorWithdrawn(address,uint256)",
    "AuthorPaid(address,uint256)",
    "VetoedByDao(address)",
    "BackerVoteCast(address,uint256,bool)",
    "ArbitrationStakeReclaimed(address,uint256)",
    "OrphanedTokensSwept(address,uint256)",
]

# All event signatures combined
EVENT_SIGNATURES = ECONOMY_EVENT_SIGNATURES + PROJECT_EVENT_SIGNATURES

# apps/homebase/config.py
# Homebase app configuration

APP_NAME = "homebase"

# Firebase credentials file path (relative to indexer root)
FIREBASE_CREDENTIALS = "homebase.json"

# Trustless Firebase credentials (for cross-writing Economy documents)
TRUSTLESS_FIREBASE_CREDENTIALS = "secrets/trustless.json"

# Network-specific configurations
NETWORKS = {
    "mainnet": {
        "firestore_doc_name": "Etherlink",
        "dao_collection_name": "idaosEtherlink",
        "trustless_network_collection": "Etherlink",
    },
    "testnet": {
        "firestore_doc_name": "Etherlink-Testnet",
        "dao_collection_name": "idaosEtherlink-Testnet",
        "trustless_network_collection": "Etherlink-Testnet",
    },
    "shadownet": {
        "firestore_doc_name": "Etherlink-Shadownet",
        "dao_collection_name": "idaosEtherlink-Shadownet",
        "trustless_network_collection": "Etherlink-Shadownet",
    },
    "localhost": {
        "firestore_doc_name": "Localhost",
        "dao_collection_name": "idaosLocalhost",
        "trustless_network_collection": "Localhost",
    },
    "base-sepolia": {
        "firestore_doc_name": "Base-Sepolia",
        "dao_collection_name": "idaosBase-Sepolia",
        "trustless_network_collection": "Base-Sepolia",
    },
}

# Event signatures that this app listens for
EVENT_SIGNATURES = [
    "NewDaoCreated(address,address,address[],uint256[],string,string,string,uint256,address,string[],string[])",
    "DaoWrappedDeploymentInfo(address,address,address,address,string,string,string,uint8,uint256,uint48,uint32,uint256)",
    "SuiteConfigured(address,address,address,address,address,address)",  # TrustlessFactory: deployer, economy, registry, timelock, repToken, dao
    "DelegateChanged(address,address,address)",
    "ProposalCreated(uint256,address,address[],uint256[],string[],bytes[],uint256,uint256,string)",
    "ProposalQueued(uint256,uint256)",
    "ProposalExecuted(uint256)",
    "VoteCast(address,uint256,uint8,uint256,string)",
    "RegistryUpdated(string,string)",  # Registry: key, value - for Economy DAO descriptions
]

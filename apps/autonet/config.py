# apps/autonet/config.py
#
# Autonet agent directory indexer. Mirrors Substrate.sol AgentRegistered /
# EndpointUpdated events into the autonet Firestore `agents` collection so the
# web app can show registered agents (and resolve an agent's 0x address to its
# browser-reachable wss endpoint) without a daemon connection.
#
# Writes to the AUTONET Firebase project (autonet-275416) via its own service
# account — distinct from homebase/trustless. The canonical substrate contract
# address + RPC are read at runtime from the `settings/network` doc (written by
# autonet's scripts/indexer/publish_network_config.py after every redeploy), so
# a contract redeploy needs no edit here — just re-run that script.

APP_NAME = "autonet"

# Service account for the autonet Firebase project (relative to indexer root).
FIREBASE_CREDENTIALS = "secrets/autonet-firebase.json"

# Network configs. Unlike the other apps, the substrate address is NOT pinned
# here — it's resolved from settings/network at startup (single source of truth
# shared with the web app). We keep the agents collection + checkpoint doc per
# network so testnet/shadownet data never mix.
NETWORKS = {
    "shadownet": {
        "firestore_doc_name": "Etherlink-Shadownet",
        "agents_collection": "agents",
    },
    "mainnet": {
        "firestore_doc_name": "Etherlink",
        "agents_collection": "agents",
    },
    "localhost": {
        "firestore_doc_name": "Localhost",
        "agents_collection": "agents",
    },
}

# Events we mirror. AgentRegistered seeds the agent's identity record;
# EndpointUpdated refreshes its mutable browser-reachable wss endpoint.
EVENT_SIGNATURES = [
    "AgentRegistered(address,bytes32,bytes,uint256)",
    "EndpointUpdated(address,string)",
]

# apps/autonet/indexer.py

from web3 import Web3

from apps.autonet.config import EVENT_SIGNATURES
from apps.autonet.paper import Paper

SETTINGS_COLLECTION = "settings"
NETWORK_DOC = "network"


def initialize(db, web3: Web3, network_config: dict):
    """Called once at startup. Returns the event-signature hash map."""
    print("[autonet] Initializing...")
    event_sigs = {}
    for sig in EVENT_SIGNATURES:
        name = sig.split("(")[0]
        h = web3.keccak(text=sig).hex()
        if not h.startswith("0x"):
            h = "0x" + h
        event_sigs[h] = name
        print(f"[autonet]   {name}: {h[:20]}...")
    print(f"[autonet] Initialized with {len(event_sigs)} event signatures")
    return {"event_signatures": event_sigs}


def _resolve_substrate_address(db, web3: Web3) -> str | None:
    """Single source of truth for which contract to follow: settings/network
    (written by autonet's publish_network_config.py after each redeploy). Same
    doc the web app reads, so the indexer never drifts from what users see.

    The autonet Firebase project holds ONE settings/network doc (the deployed
    chain). This app, however, is loaded on every indexer instance (mainnet,
    base-sepolia, shadownet, ...). So we gate on chain id: only bind the
    contract when the running instance's chain matches the doc's chainId.
    On every other instance autonet idles — no wrong-chain log scans."""
    snap = db.collection(SETTINGS_COLLECTION).document(NETWORK_DOC).get()
    if not snap.exists:
        print("[autonet] settings/network missing — run publish_network_config.py")
        return None
    data = snap.to_dict() or {}
    addr = data.get("substrateAddress", "")
    if not addr:
        print("[autonet] settings/network has no substrateAddress")
        return None
    doc_chain = int(data.get("chainId", 0) or 0)
    try:
        live_chain = web3.eth.chain_id
    except Exception:
        live_chain = 0
    if doc_chain and live_chain and doc_chain != live_chain:
        print(f"[autonet] deployed on chain {doc_chain}, this instance is "
              f"{live_chain} — idling on this network.")
        return None
    return Web3.to_checksum_address(addr)


def setup_papers(db, web3: Web3, network_config: dict):
    """Bind one Paper to the substrate contract. The address is resolved from
    settings/network rather than pinned in config, so a redeploy needs no code
    change here."""
    print("[autonet] Setting up papers...")
    address = _resolve_substrate_address(db, web3)
    if not address:
        print("[autonet] No substrate address — autonet app idle this run.")
        return {"addresses": [], "papers": {}}

    agents_collection = network_config.get("agents_collection", "agents")
    paper = Paper(address, web3, db, agents_collection)
    print(f"[autonet] Listening on substrate {address} "
          f"-> {agents_collection} collection")
    return {"addresses": [address], "papers": {address: paper}}


def run_historical_sync(db, web3: Web3, network_config: dict, alert_func):
    """Catch up missed events from the shared checkpoint. The main loop's live
    poller advances lastSyncedBlock; here we backfill from it (or a recent
    window on first run) so registrations/endpoints emitted while the indexer
    was down are captured.

    Reuses the same Paper + topic dispatch as the live path for consistency."""
    doc_name = network_config.get("firestore_doc_name", "Etherlink-Shadownet")
    address = _resolve_substrate_address(db, web3)
    if not address:
        print("[autonet] historical sync skipped — no substrate address")
        return

    checkpoint = 0
    try:
        cdoc = db.collection("contracts").document(doc_name).get()
        if cdoc.exists:
            checkpoint = int(cdoc.to_dict().get("lastSyncedBlock", 0) or 0)
    except Exception as e:
        print(f"[autonet] could not read checkpoint: {e}")

    head = web3.eth.block_number
    from_block = checkpoint + 1 if checkpoint > 0 else max(0, head - 100_000)
    if from_block > head:
        print(f"[autonet] already synced to block {checkpoint}")
        return

    agents_collection = network_config.get("agents_collection", "agents")
    paper = Paper(address, web3, db, agents_collection)
    sig_to_name = {}
    for sig in EVENT_SIGNATURES:
        h = web3.keccak(text=sig).hex()
        if not h.startswith("0x"):
            h = "0x" + h
        sig_to_name[h] = sig.split("(")[0]

    # Shadownet rejects large eth_getLogs ranges (-32062); chunk conservatively.
    CHUNK = 500
    print(f"[autonet] historical sync {from_block}..{head} on {address}")
    b = from_block
    while b <= head:
        to_b = min(b + CHUNK - 1, head)
        try:
            logs = web3.eth.get_logs({
                "address": address,
                "topics": [list(sig_to_name.keys())],
                "fromBlock": b,
                "toBlock": to_b,
            })
            for entry in logs:
                topic0 = entry["topics"][0].hex()
                topic0 = topic0 if topic0.startswith("0x") else "0x" + topic0
                name = sig_to_name.get(topic0)
                if name:
                    paper.handle_event(dict(entry), func=name)
        except Exception as e:
            print(f"[autonet] historical chunk {b}..{to_b} failed: {e}")
        b = to_b + 1
    print("[autonet] historical sync done")

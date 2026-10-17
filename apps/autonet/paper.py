# apps/autonet/paper.py

from web3 import Web3
from google.cloud import firestore


def _as_bytes(value) -> bytes:
    """Normalise a web3 log field (HexBytes / hex str) to raw bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        s = value[2:] if value.startswith("0x") else value
        return bytes.fromhex(s)
    raise TypeError(f"cannot coerce {type(value).__name__} to bytes")


def _as_hex(value) -> str:
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    return "0x" + _as_bytes(value).hex()


class Paper:
    """Handles Substrate.sol agent events for one contract address.

    One doc per agent per Substrate, agents/{chainId}:{substrate}:{agent}, since
    the same address can be an agent in several jurisdictions. Each doc carries
    chainId and substrate so readers filter to one jurisdiction. Events, keyed on
    the indexed `agent` address (topics[1]):
      - AgentRegistered  -> identity seed (peerId, registeredAt)
      - OwnerBound       -> owner and parent agent (0x0 for none)
      - AgentRevoked     -> revoked, for the agent and every descendant
      - EndpointUpdated  -> mutable wss endpoint (merge; '' clears it)
    """

    def __init__(self, address: str, web3: Web3, db, agents_collection: str):
        self.address = Web3.to_checksum_address(address)
        self.web3 = web3
        self.db = db
        self.agents_collection = agents_collection
        self.chain_id = int(web3.eth.chain_id)

    def handle_event(self, log, func=None):
        """Dispatch a matched log. Returns None (autonet adds no new contracts
        to listen to — the substrate address is fixed)."""
        try:
            if func == "AgentRegistered":
                self._handle_registered(log)
            elif func == "OwnerBound":
                self._handle_owner_bound(log)
            elif func == "AgentRevoked":
                self._handle_revoked(log)
            elif func == "EndpointUpdated":
                self._handle_endpoint(log)
        except Exception as e:
            print(f"[autonet] error handling {func}: {e}")
        return None

    # -- decoders ----------------------------------------------------------

    def _agent_from_topic(self, log, i=1) -> str:
        return Web3.to_checksum_address(
            "0x" + _as_bytes(log["topics"][i])[-20:].hex())

    def _handle_registered(self, log) -> None:
        agent = self._agent_from_topic(log)
        # Non-indexed: peerId bytes + timestamp uint256.
        peer_id_bytes, timestamp = self.web3.codec.decode(
            ["bytes", "uint256"], _as_bytes(log["data"]))
        self._write(agent, {
            "address": agent,
            "peerId": "0x" + peer_id_bytes.hex(),
            "registeredAt": int(timestamp),
            "blockNumber": int(log["blockNumber"]),
            "transactionHash": _as_hex(log["transactionHash"]),
        })
        print(f"[autonet] registered {agent} (block {int(log['blockNumber'])})")

    def _handle_owner_bound(self, log) -> None:
        agent = self._agent_from_topic(log)
        self._write(agent, {
            "address": agent,
            "owner": self._agent_from_topic(log, 2),
            "parent": self._agent_from_topic(log, 3),
            "ownerBlock": int(log["blockNumber"]),
        })

    def _handle_revoked(self, log) -> None:
        # The Substrate revokes the agent's whole subtree implicitly, through the
        # parent links; mirror that so readers needn't walk the tree.
        block = int(log["blockNumber"])
        todo, seen = [self._agent_from_topic(log)], set()
        while todo:
            agent = todo.pop()
            if agent in seen:
                continue
            seen.add(agent)
            self._write(agent, {"address": agent, "revoked": True, "revokedBlock": block})
            children = (self.db.collection(self.agents_collection)
                        .where("substrate", "==", self.address)
                        .where("chainId", "==", self.chain_id)
                        .where("parent", "==", agent).stream())
            todo += [c.to_dict()["address"] for c in children]
        print(f"[autonet] revoked {len(seen)} agent(s) from {self._agent_from_topic(log)} (block {block})")

    def _handle_endpoint(self, log) -> None:
        agent = self._agent_from_topic(log)
        # Non-indexed: wsEndpoint string. '' means the agent went dark.
        (ws_endpoint,) = self.web3.codec.decode(["string"], _as_bytes(log["data"]))
        self._write(agent, {
            "address": agent,
            "wsEndpoint": ws_endpoint,
            "endpointBlock": int(log["blockNumber"]),
        })
        print(f"[autonet] endpoint {agent} -> "
              f"{ws_endpoint or '(cleared)'} (block {int(log['blockNumber'])})")

    def _write(self, agent: str, payload: dict) -> None:
        payload.update(chainId=self.chain_id, substrate=self.address,
                       indexedAt=firestore.SERVER_TIMESTAMP)
        doc_id = f"{self.chain_id}:{self.address}:{agent}"
        self.db.collection(self.agents_collection).document(doc_id).set(
            payload, merge=True)

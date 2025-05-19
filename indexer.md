

### `app.py`
```py
from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal
from apps.homebase.paper import Paper
from datetime import datetime, timezone
import time
from firebase_admin import initialize_app
from firebase_admin import firestore, credentials
from web3 import Web3
import os
import sys
import re
cred = credentials.Certificate('homebase.json')
initialize_app(cred)
db = firestore.client()
networks = db.collection("contracts")
ceva = networks.document("Etherlink-Testnet").get()
rpc = "https://node.ghostnet.etherlink.com"
wrapper_address = ceva.to_dict()['wrapper']
wrapper_t_address = ceva.to_dict()['wrapper_t']
print("wrapper address :" + str(wrapper_address))

web3 = Web3(Web3.HTTPProvider(rpc))
papers = {}
daos = []
if web3.is_connected():
    print("node connected")
else:
    print("node connection failed!")

daos_collection = db.collection('idaosEtherlink-Testnet')
docs = list(daos_collection.stream())
dao_addresses = [doc.id for doc in docs]

for doc in docs:
    obj = doc.to_dict()
    try:
        p = Paper(address=obj['token'], kind="token",
                  daos_collection=daos_collection, db=db,  web3=web3, dao=doc.id)
        dao = Paper(address=obj['address'], kind="dao", token=p,
                    daos_collection=daos_collection, db=db,  web3=web3, dao=doc.id)
    except Exception as e:
        print("one DAO contract can't parse correctly: "+str(e))
    papers.update({obj['token']: p})
    papers.update({obj['address']: dao})

event_signatures = {
    web3.keccak(text="NewDaoCreated(address,address,address[],uint256[],string,string,string,uint256,address,string[],string[])").hex(): "NewDaoCreated",
    "0x01c5013cf023a364cc49643b8f57347e398d2f0db0968edeb64e7c41bf2dfbde": "NewDaoCreated",
    "0x3134e8a2e6d97e929a7e54011ea5485d7d196dd5f0ba4d4ef95803e8e3fc257f": "DelegateChanged",
    "0x7d84a6263ae0d98d3329bd7b46bb4e8d6f98cd35a7adb45c274c8b7fd5ebd5e0": "ProposalCreated",
    "0x9a2e42fd6722813d69113e7d0079d3d940171428df7373df9c7f7617cfda2892": "ProposalQueued",
    "0x712ae1383f79ac853f8d882153778e0260ef8f03b504e2866e0593e04d2b291f": "ProposalExecuted",
    "0xb8e138887d0aa13bab447e82de9d5c1777041ecd21ca36ba824ff1e6c07ddda4": "VoteCast"
}

papers.update({wrapper_address: Paper(address=wrapper_address,
              kind="wrapper", daos_collection=daos_collection, db=db,  web3=web3)})
papers.update({wrapper_t_address: Paper(address=wrapper_t_address,
              kind="wrapper", daos_collection=daos_collection, db=db,  web3=web3)})
listening_to_addresses = [wrapper_address, wrapper_t_address]
listening_to_addresses = listening_to_addresses+list(papers.keys())

counter = 0
processed_transactions = set()
heartbeat = 0
print(f"Listening for {len(event_signatures)} events on {len(papers.items())} contracts...")

while True:
    heartbeat += 1
    try:
        latest = web3.eth.block_number
        first = latest-13
        logs = web3.eth.get_logs({
            "fromBlock": first,
            "toBlock": latest,
            "address": listening_to_addresses,
            # "topics": [[*event_signatures.keys()], None]
        })
        for log in logs:
            tx_hash = log["transactionHash"].hex()
            if tx_hash in processed_transactions:
                print("already did this one")
                continue  # Skip duplicate
            contract_address = log["address"]
            event_signature = "0x"+log["topics"][0].hex()
            if event_signatures.get(event_signature) is not None:
                event_name = event_signatures[event_signature]
                processed_transactions.add(tx_hash)
            else:
                notfound = True
                print("not found")
                continue
            event_name = event_signatures[event_signature]
            print(f"Event: {event_name}, Contract: {contract_address}")
            print(f"Event: {event_name}, Contract: {contract_address}")
            new_contract_addresses = papers[contract_address].handle_event(
                log, func=event_name)
            if new_contract_addresses != None:
                dao_address = new_contract_addresses[0]
                token_address = new_contract_addresses[1]
                print("adding dao "+dao_address+" and token "+token_address)
                listening_to_addresses = listening_to_addresses + \
                    [dao_address] + [token_address]
                print("latest addresses added " +
                      str(listening_to_addresses[-1]+", "+str(listening_to_addresses[-2])))
                p: Paper = Paper(address=token_address, kind="token",
                                 daos_collection=daos_collection, db=db, dao=dao_address, web3=web3)
                papers.update({token_address: p})
                papers.update({dao_address: Paper(token=p,
                                                  address=dao_address, kind="dao", daos_collection=daos_collection, db=db, dao=dao_address, web3=web3)})

    except Exception as e:
        print("something went wrong "+str(e))
        web3 = Web3(Web3.HTTPProvider(rpc))
        if web3.is_connected():
            print("node connected")
        else:
            print("node connection failed!")
            os.execl(sys.executable, sys.executable, *sys.argv)
    if heartbeat % 50 == 0:
        print("heartbeat: "+str(heartbeat))

    time.sleep(3)

```

### `homebase/entities.py`

```
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Union
from enum import Enum


class Member:
    def __init__(self, address, delegate, personalBalance, votingWeight) -> None:
        self.address = address
        self.delegate = delegate
        self.personalBalance = personalBalance
        self.constituents = []
        self.votingWeight = votingWeight
        self.proposalsVoted = []
        self.proposalsCreated = []

    def toJson(self):
        return {
            'address': self.address,
            'delegate': self.delegate,
            'personalBalance': str(self.personalBalance),
            'votingWeight': str(self.votingWeight),
            'constituents': self.constituents,
            'proposalsVoted': self.proposalsVoted,
            'proposalsCreated': self.proposalsCreated,
            'lastSeen': datetime.now(timezone.utc)
        }


class ProposalStatus(Enum):
    pending = 'pending'
    active = 'active'
    passed = 'passed'
    queued = 'queued'
    executable = 'executable'
    executed = 'executed'
    expired = 'expired'
    noQuorum = 'noQuorum'
    rejected = 'rejected'


class StateInContract(Enum):
    Pending = 0
    Active = 1
    Canceled = 2
    Defeated = 3
    Succeeded = 4
    Queued = 5
    Expired = 6
    Executed = 7


class Txaction:
    def toJson(self):
        pass  # Implement as needed


class Token:
    def __init__(self, name: str, symbol: str, decimals: Optional[int]):
        self.name: str = name
        self.symbol: str = symbol
        self.decimals: Optional[int] = decimals
        self.address: Optional[str] = None

    @classmethod
    def fromJson(cls, json_data: Dict[str, Union[str, int]]):
        name = json_data['name']
        symbol = json_data['symbol']
        decimals = json_data.get('decimals')
        address = json_data.get('address')
        token = cls(name=name, symbol=symbol, decimals=decimals)
        token.address = address
        return token

    def toJson(self):
        return {
            'name': self.name,
            'symbol': self.symbol,
            'decimals': self.decimals,
            'address': self.address,
        }


class Org:
    def __init__(self, name: str, govToken: Optional[Token] = None, description: Optional[str] = None, govTokenAddress: Optional[str] = None):
        self.pollsCollection = None
        self.votesCollection = None
        self.name = name
        self.govToken = govToken
        self.description = description
        self.govTokenAddress = govTokenAddress
        self.creationDate: Optional[datetime] = None
        self.memberAddresses: Dict[str, Member] = {}
        self.symbol: Optional[str] = None
        self.decimals: Optional[int] = None
        self.proposalThreshold: Optional[str] = 0
        self.totalSupply: Optional[str] = 0
        self.nonTransferrable: bool = False
        self.treasuryAddress: Optional[str] = None
        self.registryAddress: Optional[str] = None
        self.proposals: List['Proposal'] = []
        self.proposalIDs: Optional[List[str]] = []
        self.treasuryMap: Dict[str, str] = {}
        self.registry: Dict[str, str] = {}
        self.treasury: Dict[Token, str] = {}
        self.address: Optional[str] = None
        self.holders: int = 1
        self.quorum: int = 0
        self.votingDelay: int = 0
        self.votingDuration: int = 0
        self.nativeBalance: str = "0"
        self.executionDelay: int = 0

    def toJson(self):
        return {
            'name': self.name,
            'creationDate': self.creationDate,
            'description': self.description,
            'token': self.govTokenAddress,
            'treasuryAddress': self.treasuryAddress,
            'registryAddress': self.registryAddress,
            'address': self.address,
            'holders': self.holders,
            'symbol': self.symbol,
            'decimals': self.decimals,
            'proposals': self.proposalIDs,
            'proposalThreshold': str(self.proposalThreshold),
            'registry': self.registry if self.registry != None else {},
            'treasury': {token.toJson(): value for token, value in self.treasury.items()},
            'votingDelay': self.votingDelay,
            'totalSupply': self.totalSupply,
            'votingDuration': self.votingDuration,
            'executionDelay': self.executionDelay,
            'quorum': self.quorum,
            'nonTransferrable': self.nonTransferrable,
        }


class Proposal:
    def __init__(self, org: Org, name: Optional[str] = None):
        self.id: Optional[str] = ""
        self.inAppnumber: int = 0
        self.state: Optional[ProposalStatus] = None
        self.hash: str = ""
        self.totalSupply: str = "0"
        self.org: Org = org
        self.type: Optional[str] = None
        self.name: Optional[str] = name if name else "Title of the proposal (max 80 characters)"
        self.description: Optional[str] = "(no description)"
        self.author: Optional[str] = None
        self.value: float = 0.0
        self.targets: List[str] = []
        self.values: List[str] = []
        self.executionHash = ""
        self.callDatas: List = []
        self.callData: Optional[str] = "0x"
        self.createdAt: Optional[datetime] = datetime.now(timezone.utc)
        self.votingStarts: Optional[datetime] = None
        self.votingEnds: Optional[datetime] = None
        self.executionStarts: Optional[datetime] = None
        self.executionEnds: Optional[datetime] = None
        self.status: str = ""
        self.statusHistory: Dict[str, datetime] = {
            "pending": datetime.now(timezone.utc)}
        self.latestStage = "pending"
        self.turnoutPercent: int = 0
        self.votingStartsBlock: Optional[int] = None
        self.votingEndsBlock: Optional[int] = None
        self.executionStartsBlock: Optional[int] = None
        self.executionEndsBlock: Optional[int] = None
        self.inFavor: str = "0"
        self.against: str = "0"
        self.votesFor: int = 0
        self.votesAgainst: int = 0
        self.externalResource: Optional[str] = "(no link provided)"
        self.transactions: List[Txaction] = []
        self.votes: List['Vote'] = []

    def toJson(self):
        return {
            'hash': self.hash,
            'type': self.type,
            'title': self.name,
            'description': self.description,
            'author': self.author,
            'calldata': self.callData,
            'createdAt': self.createdAt,
            'callDatas': self.callDatas,
            'targets': self.targets,
            'totalSupply': self.totalSupply,
            'values': self.values,
            'executionHash': self.executionHash,
            'statusHistory': self.statusHistory,
            'turnoutPercent': self.turnoutPercent,
            'inFavor': self.inFavor,
            'against': self.against,
            'votesFor': self.votesFor,
            'latestStage': self.latestStage,
            'votesAgainst': self.votesAgainst,
            'externalResource': self.externalResource,
            'transactions': [tx.toJson() for tx in self.transactions],
        }

    def fromJson(self, firestore_data: Dict) -> None:
        """
        Method to populate the Proposal object from Firestore data.

        Args:
            firestore_data (Dict): The Firestore data as a dictionary.
        """
        self.id = firestore_data.get('id', "")
        self.state = firestore_data.get('state')
        self.hash = firestore_data.get('hash', "")
        self.type = firestore_data.get('type')
        self.totalSupply = firestore_data.get('totalSupply', "0")
        self.name = firestore_data.get('title', self.name)
        self.description = firestore_data.get('description', self.description)
        self.author = firestore_data.get('author')
        self.value = firestore_data.get('value', 0.0)
        self.targets = firestore_data.get('targets', [])
        self.values = firestore_data.get('values', [])
        self.callDatas = firestore_data.get('callDatas', [])
        self.callData = firestore_data.get('calldata', "0x")
        self.createdAt = firestore_data.get(
            'createdAt', datetime.now(timezone.utc))
        self.votingStarts = firestore_data.get('votingStarts')
        self.votingEnds = firestore_data.get('votingEnds')
        self.executionStarts = firestore_data.get('executionStarts')
        self.executionEnds = firestore_data.get('executionEnds')
        self.status = firestore_data.get('status', "")
        self.statusHistory = firestore_data.get(
            'statusHistory', {"pending": datetime.now(timezone.utc)})
        self.latestStage = firestore_data.get('latestStage', "pending")
        self.turnoutPercent = firestore_data.get('turnoutPercent', 0)
        self.votingStartsBlock = firestore_data.get('votingStartsBlock')
        self.votingEndsBlock = firestore_data.get('votingEndsBlock')
        self.executionStartsBlock = firestore_data.get('executionStartsBlock')
        self.executionEndsBlock = firestore_data.get('executionEndsBlock')
        self.inFavor = firestore_data.get('inFavor', "0")
        self.executionHash = firestore_data.get('executionHash', "")
        self.against = firestore_data.get('against', "0")
        self.votesFor = firestore_data.get('votesFor', 0)
        self.votesAgainst = firestore_data.get('votesAgainst', 0)
        self.externalResource = firestore_data.get(
            'externalResource', "(no link provided)")

        # Deserialize transactions if present
        transactions_data = firestore_data.get('transactions', [])
        # Assuming Txaction has a `fromJson` method
        self.transactions = [Txaction.fromJson(tx) for tx in transactions_data]

        # Deserialize votes if present
        votes_data = firestore_data.get('votes', [])
        # Assuming Vote has a `fromJson` method
        self.votes = [Vote.fromJson(vote) for vote in votes_data]


class Vote:
    def __init__(self, votingPower: str, voter: str, proposalID: str, option: int, castAt=None):
        self.voter: str = voter
        self.hash = ""
        self.proposalID: str = proposalID
        self.option: int = option
        self.reason: Optional[str] = None
        self.votingPower: str = votingPower
        self.castAt: datetime = castAt if castAt else datetime.now(
            timezone.utc)

    def toJson(self):
        return {
            'weight': self.votingPower,
            'cast': self.castAt.isoformat(),
            'voter': self.voter,
            'reason': self.reason,
            'option': self.option,
            'hash': self.hash
        }

```

### `homebase/paper.py`

```
from apps.homebase.abis import wrapperAbi, daoAbiGlobal, tokenAbiGlobal, mint_function_abi, burn_function_abi
from datetime import datetime, timezone
from apps.homebase.entities import ProposalStatus, Proposal, StateInContract, Txaction, Token, Member, Org, Vote
import re
from web3 import Web3
from google.cloud import firestore
import codecs
from apps.generic.converting import decode_function_parameters
from apps.homebase.eventSignatures import quorum_function_abi, voting_period_function_abi,proposal_threshold_function_abi, voting_delay_function_abi


class Paper:
    ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
    def __init__(self, address, kind, web3, daos_collection, db, dao=None, token=None):
        self.address = address
        self.kind = kind
        self.contract = None
        self.dao = dao
        self.token: Paper = token
        self.web3 = web3
        self.daos_collection = daos_collection
        self.db = db
        if kind == "wrapper":
            self.abi = re.sub(r'\n+', ' ', wrapperAbi).strip()
        elif kind == "token":
            self.abi = re.sub(r'\n+', ' ', tokenAbiGlobal).strip()
        else:
            self.abi = re.sub(r'\n+', ' ', daoAbiGlobal).strip()

    def get_contract(self):
        if self.contract == None:
            self.contract = self.web3.eth.contract(
                address=self.address, abi=self.abi)
        return self.contract

    def get_token_contract(self):
        tokenAddress = self.token.address
        return self.web3.eth.contract(address=tokenAddress, abi=tokenAbiGlobal)

    def add_dao(self, log):
        decoded_event = self.get_contract().events.NewDaoCreated().process_log(log)
        name = decoded_event['args']['name']
        print("new dao detected: "+name)
        org: Org = Org(name=name)
        org.creationDate = datetime.now()
        org.govTokenAddress = decoded_event['args']['token']
        org.address = decoded_event['args']['dao']
        org.symbol = decoded_event['args']['symbol']
        org.registryAddress = decoded_event['args']['registry']
        org.description = decoded_event['args']['description']
        members = decoded_event['args']['initialMembers']
        amounts = decoded_event['args']['initialAmounts']
        org.holders = len(members)
        token_contract = self.web3.eth.contract(
            address=org.govTokenAddress, abi=tokenAbiGlobal)
        org.decimals = token_contract.functions.decimals().call()
        supply = 0
        batch = self.db.batch()
        for num in range(len(members)):
            m: Member = Member(
                address=members[num], personalBalance=f"{str(amounts[num])}", delegate="", votingWeight="0")
            member_doc_ref = self.daos_collection \
                .document(org.address) \
                .collection('members') \
                .document(m.address)
            batch.set(reference=member_doc_ref, document_data=m.toJson())
            supply = supply+amounts[num]
        org.totalSupply = str(supply)
        keys = decoded_event['args']['keys']
        print("lenth keys: " + str(len(keys)))
        values = decoded_event['args']['values']
        if not len(keys) > 1:
            print("it's not zero "+str(keys))

            org.registry = {keys[i]: values[i] for i in range(
                len(keys)) if keys[i] != "" and values[i] != ""}
        else:
            print("it's zero")
            org.registry = {}
        org.quorum = decoded_event['args']['initialAmounts'][-1]
        org.proposalThreshold = decoded_event['args']['initialAmounts'][-2]
        org.votingDuration = decoded_event['args']['initialAmounts'][-3]
        org.treasuryAddress = "0xFdEe849bA09bFE39aF1973F68bA8A1E1dE79DBF9"
        org.votingDelay = decoded_event['args']['initialAmounts'][-4]
        org.executionDelay = decoded_event['args']['executionDelay']
        self.daos_collection.document(org.address).set(org.toJson())
        batch.commit()
        return [org.address, org.govTokenAddress]

    def delegate(self, log):
        contract = self.get_contract()
        data = contract.events.DelegateChanged().process_log(log)
        delegator = data['args']['delegator']
        fromDelegate = data['args']['fromDelegate']
        toDelegate = data['args']['toDelegate']
        batch = self.db.batch()
        delegator_doc_ref = self.daos_collection \
            .document(self.dao) \
            .collection('members') \
            .document(delegator)
        batch.update(delegator_doc_ref, {"delegate": toDelegate, })
        if delegator != toDelegate:
            print("delegating to someone else")
            toDelegate_doc_ref = self.daos_collection \
                .document(self.dao) \
                .collection('members') \
                .document(toDelegate).collection("constituents").document(delegator)
            batch.update(toDelegate_doc_ref, {"address": delegator})

            if fromDelegate and fromDelegate != self.ZERO_ADDRESS and fromDelegate != delegator:
                fromDelegate_doc_ref = self.daos_collection \
                    .document(self.dao) \
                    .collection('members') \
                    .document(fromDelegate) \
                    .collection("constituents") \
                    .document(delegator)
                batch.delete(fromDelegate_doc_ref)
        batch.commit()
        return None

    def propose(self, log):
        event = self.get_contract().events.ProposalCreated().process_log(log)
        proposal_id = event["args"]["proposalId"]
        proposer = event["args"]["proposer"]
        address = event['address']
        targets = event["args"]["targets"]
        values = event["args"]["values"]
        signatures = event["args"]["signatures"]
        calldatas = event["args"]["calldatas"]
        vote_start = event["args"]["voteStart"]
        vote_end = event["args"]["voteEnd"]
        description = event["args"]["description"]
        parts = description.split("0|||0")
        if len(parts) > 3:
            name = parts[0]
            type_ = parts[1]
            desc = parts[2]
            link = parts[3]
        else:
            name = "(no title)"
            type_ = "registry"
            desc = description
            link = "(no link)"
        p: Proposal = Proposal(name=name, org=address)
        p.author = proposer
        p.id = proposal_id
        p.type = type_
        p.targets = targets
        p.values = list(map(str, values))
        p.description = desc
        p.callDatas = calldatas
        contract = self.get_token_contract()
        p.totalSupply = contract.functions.totalSupply().call()
        p.createdAt = datetime.now(tz=timezone.utc)
        p.votingStartsBlock = str(vote_start)
        p.votingEndsBlock = str(vote_end)
        p.externalResource = link
        proposal_doc_ref = self.daos_collection \
            .document(self.dao) \
            .collection('proposals') \
            .document(str(proposal_id))
        proposal_doc_ref.set(p.toJson())

        member_doc_ref = self.daos_collection \
            .document(self.dao) \
            .collection('members') \
            .document(str(proposer))
        member_doc_ref.update(
            {"proposalsCreated": firestore.ArrayUnion([str(proposal_id)])})

    def vote(self, log):
        event = self.get_contract().events.VoteCast().process_log(log)
        proposal_id = str(event["args"]["proposalId"])
        address = event['address']
        hash = event['transactionHash']
        voter = event["args"]["voter"]
        support = event["args"]["support"]
        weight = event["args"]["weight"]
        reason = event["args"]["reason"]
        vote: Vote = Vote(proposalID=str(proposal_id), votingPower=str(
            weight), option=support, voter=voter)
        vote.reason = reason
        vote.hash = hash
        vote_doc_ref = self.daos_collection \
            .document(self.dao) \
            .collection('proposals') \
            .document(proposal_id).collection("votes").document(voter)
        vote_doc_ref.set(vote.toJson())

        member_doc_ref = self.daos_collection \
            .document(self.dao) \
            .collection('members') \
            .document(str(voter))
        member_doc_ref.update(
            {"proposalsVoted": firestore.ArrayUnion([str(proposal_id)])})
        proposal_doc_ref = self.daos_collection \
            .document(self.dao) \
            .collection('proposals') \
            .document(proposal_id)
        ceva = proposal_doc_ref.get()
        data = ceva.to_dict()
        prop: Proposal = Proposal(name="whatever", org=None)
        prop.fromJson(data)
        if support == 1:
            prop.inFavor = str(int(prop.inFavor)+int(weight))
            prop.votesFor += 1
        elif support == 0:
            prop.against = str(int(prop.against)+int(weight))
            prop.votesAgainst += 1
        proposal_doc_ref.set(prop.toJson())

    def queue(self, log):
        event = self.get_contract().events.ProposalQueued().process_log(log)
        proposal_id = str(event['args']['proposalId'])
        proposal_doc_ref = self.daos_collection \
            .document(self.dao) \
            .collection('proposals') \
            .document(proposal_id)
        proposal_doc_ref.update(
            {"statusHistory.queued": datetime.now(tz=timezone.utc)})

    def bytes_to_int(self, byte_array):
        return int.from_bytes(byte_array, byteorder='big')

    def decode_params(self, data_bytes):
        data_without_selector = data_bytes[4:]
        param1_offset_bytes = data_without_selector[:32]
        param2_offset_bytes = data_without_selector[32:64]
        param1_offset = self.bytes_to_int(param1_offset_bytes)
        param2_offset = self.bytes_to_int(param2_offset_bytes)
        param1_length_bytes = data_without_selector[param1_offset:param1_offset + 32]
        param1_length = self.bytes_to_int(param1_length_bytes)
        param1_data_bytes = data_without_selector[param1_offset +
                                                  32:param1_offset + 32 + param1_length]
        param1_data = param1_data_bytes.decode('utf-8')
        param2_length_bytes = data_without_selector[param2_offset:param2_offset + 32]
        param2_length = self.bytes_to_int(param2_length_bytes)
        param2_data_bytes = data_without_selector[param2_offset +
                                                  32:param2_offset + 32 + param2_length]
        param2_data = param2_data_bytes.decode('utf-8')
        return param1_data, param2_data

     
    def execute(self, log):
        try:
            print("executing proposal ")
            event = self.get_contract().events.ProposalExecuted().process_log(log)
            proposal_id = str(event['args']['proposalId'])
            print("id: "+proposal_id)
            proposal_doc_ref = self.daos_collection \
                .document(self.dao) \
                .collection('proposals') \
                .document(proposal_id)
            ceva = proposal_doc_ref.get()
            data = ceva.to_dict()
            prop: Proposal = Proposal(name="whatever", org=None)
            prop.fromJson(data)
            prop.executionHash = str(event['transactionHash'])
            if "voting period" in prop.type.lower():
                hex_string = prop.callDatas[0]
                decoded = decode_function_parameters(voting_period_function_abi, prop.callDatas[0])
                new_voting_period = int(decoded[0])//60
                dao_doc_ref = self.daos_collection \
                    .document(self.dao)
                dao_doc_ref.update({"votingDuration": new_voting_period})
            if "threshold" in prop.type.lower():
                print("we got threshold")
                hex_string = prop.callDatas[0]
                decoded = decode_function_parameters(proposal_threshold_function_abi, prop.callDatas[0])
                new_voting_period = int(decoded[0])

                dao_doc_ref = self.daos_collection \
                    .document(self.dao)
                altceva = dao_doc_ref.get().to_dict()
                decimals=int(altceva['decimals'])
                new_proposal_threshold = int(new_voting_period//10**decimals)
                dao_doc_ref.update({"proposalThreshold": str(new_proposal_threshold)})
            if "delay" in prop.type.lower():
                hex_string = prop.callDatas[0]
                decoded = decode_function_parameters(voting_delay_function_abi, prop.callDatas[0])
                new_voting_period = int(decoded[0])//60
                dao_doc_ref = self.daos_collection \
                    .document(self.dao)
                dao_doc_ref.update({"votingDelay": new_voting_period})

            if "quorum" in prop.type.lower():
                hex_string = prop.callDatas[0]
                decoded = decode_function_parameters(quorum_function_abi, prop.callDatas[0])
                dao_doc_ref = self.daos_collection \
                    .document(self.dao)
                dao_doc_ref.update({"quorum":  int(decoded[0])})
            if prop.type == "registry":
                hex_string = prop.callDatas[0]
                param1, param2 = self.decode_params(hex_string)
                dao_doc_ref = self.daos_collection \
                    .document(self.dao)
                altceva = dao_doc_ref.get()
                datat = altceva.to_dict()
                registry = datat.get("registry", [])
                registry[param1] = param2
                dao_doc_ref.update({"registry": registry})
            proposal_doc_ref.update(
                {"statusHistory.executed": datetime.now(tz=timezone.utc),
                 "executionHash": str(event['transactionHash'])
                 })
            if "mint" in prop.type.lower() or "burn" in prop.type.lower():
                print("we got mint or burn")
                token_contract = self.web3.eth.contract(
                    address=Web3.to_checksum_address(prop.targets[0]), abi=tokenAbiGlobal)
                params = decode_function_parameters(
                    function_abi=mint_function_abi, data_bytes=prop.callDatas[0])
                memberAddress = Web3.to_checksum_address(params[0])
                balance = token_contract.functions.balanceOf(
                    memberAddress).call()
                member_doc_ref = self.daos_collection \
                    .document(self.dao) \
                    .collection('members') \
                    .document(memberAddress)
                doc = member_doc_ref.get()
                if doc.exists:
                    member_doc_ref.update({"personalBalance": str(balance)})
                else:
                    m:Member=Member(address=memberAddress, personalBalance=str(balance), delegate="", votingWeight="0")
                    member_doc_ref.set(m.toJson())
                member_doc_ref.set({"personalBalance": str(balance)}, merge=True)
                supply = token_contract.functions.totalSupply().call()
                dao_doc_ref = self.daos_collection \
                    .document(self.dao)
                dao_doc_ref.update({"totalSupply": str(supply)})

        except Exception as e:
            print("execution error "+str(e))

    def handle_event(self, log, func=None):
        if self.kind == "wrapper":
            return self.add_dao(log)
        if self.kind == "token":
            self.delegate(log)
        if self.kind == "dao":
            if func == "ProposalCreated":
                self.propose(log)
            elif func == "VoteCast":
                self.vote(log)
            elif func == "ProposalQueued":
                self.queue(log)
            elif func == "ProposalExecuted":
                self.execute(log)

```

### `eventSignatures.py`

```
quorum_function_abi = {
    "name": "quorum",
    "inputs": [
        {"name": "newQuorumNumerator", "type": "uint256"},
    ],
}


voting_period_function_abi = {
    "name": "setVotingPeriod",
    "inputs": [
        {"name": "newVotingPeriod", "type": "uint32"},
    ],
}

proposal_threshold_function_abi = {
    "name": "setProposalThreshold",
    "inputs": [
        {"name": "newProposalThreshold", "type": "uint256"},
    ],
}
voting_delay_function_abi = {
    "name": "setVotingDelay",
    "inputs": [
        {"name": "newVotingDelay", "type": "uint48"},
    ],
}



```
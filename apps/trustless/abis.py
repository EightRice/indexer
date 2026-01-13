# apps/trustless/abis.py
# ABIs for decoding events and reading contract state

# Economy contract ABI - NewProject event + read functions
economyAbi = '''[
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "internalType": "address", "name": "contractAddress", "type": "address"},
            {"indexed": false, "internalType": "string", "name": "projectName", "type": "string"},
            {"indexed": false, "internalType": "address", "name": "contractor", "type": "address"},
            {"indexed": false, "internalType": "address", "name": "arbiter", "type": "address"},
            {"indexed": false, "internalType": "string", "name": "termsHash", "type": "string"},
            {"indexed": false, "internalType": "string", "name": "repo", "type": "string"},
            {"indexed": false, "internalType": "string", "name": "description", "type": "string"},
            {"indexed": false, "internalType": "address", "name": "token", "type": "address"}
        ],
        "name": "NewProject",
        "type": "event"
    },
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    }
]'''

# Project contract ABI for reading state (used in state sync)
projectReadAbi = '''[
    {
        "inputs": [],
        "name": "stage",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "projectValue",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "arbitrationFeePaidOut",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "disputeResolution",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]'''

# NativeProject / ERC20Project ABI - all project lifecycle events
projectAbi = '''[
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "_contractor", "type": "address"},
            {"indexed": false, "internalType": "address", "name": "_arbiter", "type": "address"},
            {"indexed": false, "internalType": "string", "name": "_termsHash", "type": "string"}
        ],
        "name": "SetParties",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "who", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "howMuch", "type": "uint256"},
            {"indexed": false, "internalType": "uint256", "name": "immediateBps", "type": "uint256"}
        ],
        "name": "SendFunds",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "contractor", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "ImmediateFundsReleased",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "contractor", "type": "address"}
        ],
        "name": "ContractSigned",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "by", "type": "address"}
        ],
        "name": "ProjectDisputed",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "by", "type": "address"}
        ],
        "name": "ProjectClosed",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "arbiter", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "percent", "type": "uint256"},
            {"indexed": false, "internalType": "string", "name": "rulingHash", "type": "string"}
        ],
        "name": "ArbitrationDecision",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "internalType": "address", "name": "appealer", "type": "address"},
            {"indexed": true, "internalType": "uint256", "name": "proposalId", "type": "uint256"}
        ],
        "name": "ArbitrationAppealed",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "internalType": "address", "name": "finalizer", "type": "address"}
        ],
        "name": "ArbitrationFinalized",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "internalType": "address", "name": "timelock", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "percent", "type": "uint256"},
            {"indexed": false, "internalType": "string", "name": "rulingHash", "type": "string"}
        ],
        "name": "DaoOverruled",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "contractor", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "ContractorPaid",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "contributor", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "ContributorWithdrawn",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": false, "internalType": "address", "name": "author", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "AuthorPaid",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "internalType": "address", "name": "timelock", "type": "address"}
        ],
        "name": "VetoedByDao",
        "type": "event"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "internalType": "address", "name": "voter", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "votingPower", "type": "uint256"},
            {"indexed": false, "internalType": "bool", "name": "forDispute", "type": "bool"}
        ],
        "name": "BackerVoteCast",
        "type": "event"
    }
]'''


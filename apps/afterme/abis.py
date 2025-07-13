# indexer/apps/afterme/abis.py

# ABI for the Source contract that creates Wills
source_abi = '''
[
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "owner",
        "type": "address"
      },
      {
        "indexed": true,
        "internalType": "address",
        "name": "willContract",
        "type": "address"
      }
    ],
    "name": "WillCreated",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "owner",
        "type": "address"
      },
      {
        "indexed": true,
        "internalType": "address",
        "name": "willContract",
        "type": "address"
      }
    ],
    "name": "WillCleared",
    "type": "event"
  }
]
'''

# ABI for the individual Will contracts
will_abi = '''
[
  {
    "inputs": [
      { "internalType": "address", "name": "initialOwner", "type": "address" },
      { "internalType": "address[]", "name": "_heirs", "type": "address[]" },
      { "internalType": "uint256[]", "name": "_distro", "type": "uint256[]" },
      { "internalType": "uint256", "name": "_interval", "type": "uint256" },
      { "internalType": "address[]", "name": "_erc20Contracts", "type": "address[]" },
      { "internalType": "address[]", "name": "_nftContracts", "type": "address[]" },
      { "internalType": "uint256[]", "name": "_nftTokenIds", "type": "uint256[]" },
      { "internalType": "address[]", "name": "_nftHeirs", "type": "address[]" },
      { "internalType": "address", "name": "_sourceContract", "type": "address" },
      { "internalType": "uint256", "name": "_terminationFee", "type": "uint256" }
    ],
    "stateMutability": "payable",
    "type": "constructor"
  },
  {
    "anonymous": false,
    "inputs": [
      { "indexed": false, "internalType": "uint256", "name": "feePaid", "type": "uint256" }
    ],
    "name": "Cancelled",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      { "indexed": false, "internalType": "address", "name": "executor", "type": "address" },
      { "indexed": false, "internalType": "uint256", "name": "ethFee", "type": "uint256" },
      { "indexed": false, "internalType": "address", "name": "feeRecipient", "type": "address" }
    ],
    "name": "Executed",
    "type": "event"
  },
  {
    "anonymous": false,
    "inputs": [
      { "indexed": false, "internalType": "uint256", "name": "newLastUpdate", "type": "uint256" }
    ],
    "name": "Ping",
    "type": "event"
  },
  {
    "inputs": [],
    "name": "getWillDetails",
    "outputs": [
      {
        "components": [
          { "internalType": "address", "name": "owner", "type": "address" },
          { "internalType": "uint256", "name": "interval", "type": "uint256" },
          { "internalType": "uint256", "name": "lastUpdate", "type": "uint256" },
          { "internalType": "bool", "name": "executed", "type": "bool" },
          { "internalType": "uint256", "name": "ethBalance", "type": "uint256" },
          { "internalType": "address[]", "name": "heirs", "type": "address[]" },
          { "internalType": "uint256[]", "name": "distributionPercentages", "type": "uint256[]" },
          {
            "components": [
              { "internalType": "address", "name": "tokenContract", "type": "address" },
              { "internalType": "uint256", "name": "balance", "type": "uint256" }
            ],
            "internalType": "struct Erc20Detail[]",
            "name": "erc20Details",
            "type": "tuple[]"
          },
          {
            "components": [
              { "internalType": "address", "name": "tokenContract", "type": "address" },
              { "internalType": "uint256", "name": "tokenId", "type": "uint256" },
              { "internalType": "address", "name": "heir", "type": "address" }
            ],
            "internalType": "struct Erc721Detail[]",
            "name": "erc721Details",
            "type": "tuple[]"
          }
        ],
        "internalType": "struct WillDetails",
        "name": "",
        "type": "tuple"
      }
    ],
    "stateMutability": "view",
    "type": "function"
  }
]
'''
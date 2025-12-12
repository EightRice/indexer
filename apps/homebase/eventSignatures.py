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


mint_function_abi = {
    "name": "mint",
    "inputs": [
        {"name": "to", "type": "address"},
        {"name": "amount", "type": "uint256"}
    ],
}

burn_function_abi = {
    "name": "burn",
    "inputs": [
        {"name": "from", "type": "address"},
        {"name": "amount", "type": "uint256"}
    ],
}




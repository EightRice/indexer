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



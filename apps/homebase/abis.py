# ... (existing ABIs for setX functions) ...

# ABIs for GETTING current DAO settings (from Governor and Timelock)
# These are simplified examples; ensure they match your actual contract interfaces.

# From Governor (e.g., HomebaseDAO which inherits GovernorSettings)
governor_proposal_threshold_abi = {
    "name": "proposalThreshold",
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}],
}

governor_voting_delay_abi = {
    "name": "votingDelay", # in blocks
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}], # OZ Governor returns uint256 for blocks
}

governor_voting_period_abi = {
    "name": "votingPeriod", # in blocks
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}], # OZ Governor returns uint256 for blocks
}

# From Governor (GovernorTimelockControl)
governor_timelock_abi = {
    "name": "timelock", # Gets the address of the TimelockController
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "address"}],
}

# From TimelockController
timelock_min_delay_abi = {
    "name": "getMinDelay", # Timelock's execution delay in seconds
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}],
}

# From ERC20Wrapper (HBEVM_Wrapped_Token)
erc20_wrapper_underlying_abi = {
    "name": "underlying",
    "type": "function",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "address"}],
}

# Keep your existing ABIs for setX functions if they are used by decode_function_parameters
quorum_function_abi = { # This is for SETTING quorum (updateQuorumNumerator usually)
    "name": "setQuorumNumerator", # Or updateQuorumNumerator if that's the actual function in GovernorVotesQuorumFraction
    "inputs": [
        {"name": "newQuorumNumerator", "type": "uint256"},
    ],
}

voting_period_function_abi = { # This is for SETTING
    "name": "setVotingPeriod",
    "inputs": [
        {"name": "newVotingPeriod", "type": "uint32"}, # In blocks for OZ Governor
    ],
}

proposal_threshold_function_abi = { # This is for SETTING
    "name": "setProposalThreshold",
    "inputs": [
        {"name": "newProposalThreshold", "type": "uint256"},
    ],
}
voting_delay_function_abi = { # This is for SETTING
    "name": "setVotingDelay",
    "inputs": [
        {"name": "newVotingDelay", "type": "uint48"}, # In blocks for OZ Governor
    ],
}
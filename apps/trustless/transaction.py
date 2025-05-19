from web3 import Web3

# Connect to an Infura endpoint
infura_url = rpc
web3 = Web3(Web3.HTTPProvider(infura_url))

# Check connection
if not web3.is_connected():
    raise ConnectionError("Failed to connect to Infura")

# Define transaction details
sender_address = "sender"
receiver_address = "reciever"
private_key = "pk"  # Keep this secure

# Convert 0.5 ETH to Wei
value_in_wei = web3.to_wei(0.5, 'ether')


# Get the nonce
nonce = web3.eth.get_transaction_count(sender_address)
gas_limit = web3.eth.estimate_gas({
    'to': receiver_address,
    'from': sender_address,
    'value': value_in_wei
})
# Build the transaction
transaction = {
    'to': receiver_address,
    'value': value_in_wei,
    'gas': gas_limit,
    'gasPrice': web3.eth.gas_price,
    'nonce': nonce,
    'chainId': 128123  # Mainnet chain ID
}

# Sign the transaction
signed_tx = web3.eth.account.sign_transaction(transaction, private_key)

# Send the transaction
tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

# Print the transaction hash
print(f"Transaction sent! Hash: {tx_hash.hex()}")

from web3 import Web3
import json
import time

# Connect to the Ethereum node
web3 = Web3(Web3.HTTPProvider(rpc))

if not web3.is_connected():
    print("Failed to connect to Ethereum node")
    exit()

print(f"Connected to Ethereum node: {web3.is_connected()}")

# ERC20 Transfer Event Signature
TRANSFER_EVENT_SIGNATURE = web3.keccak(
    text="Transfer(address,address,uint256)").hex()
ADDRESS_TO_MONITOR = "0x9C204994227e4A42A94470a1b483164aF11454B3"


def handle_event(event):
    # Decode the log data
    topics = event["topics"]
    data = event["data"]

    # Decode the sender and recipient from the topics
    sender = web3.toChecksumAddress("0x" + topics[1].hex()[26:])
    recipient = web3.toChecksumAddress("0x" + topics[2].hex()[26:])

    # Decode the value (amount transferred)
    value = int(data, 16)

    # Check if the transfer is to the monitored address
    if recipient.lower() == ADDRESS_TO_MONITOR.lower():
        print(f"Transfer detected to {ADDRESS_TO_MONITOR}:")
        print(f"  From: {sender}")
        print(f"  Value: {web3.fromWei(value, 'ether')} tokens")


def log_loop(event_filter, poll_interval):
    print(f"Listening for events to {ADDRESS_TO_MONITOR}...")
    while True:
        for event in event_filter.get_new_entries():
            handle_event(event)
        time.sleep(poll_interval)


# Create a filter for Transfer events
event_filter = web3.eth.filter({
    "fromBlock": "latest",
    "topics": [TRANSFER_EVENT_SIGNATURE, None, web3.to_hex(hexstr=ADDRESS_TO_MONITOR)]
})


# Start listening
try:
    log_loop(event_filter, 2)
except KeyboardInterrupt:
    print("\nExiting...")

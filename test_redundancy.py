"""Test script for redundancy protocol."""

import time
from redundancy import RedundancyProtocol

def test_protocol():
    """Test basic UDP communication between two instances."""

    # Callbacks for primary
    def on_primary_register(peer_address, peer_data):
        print(f"[PRIMARY] Peer registered: {peer_address}")

    def on_primary_heartbeat(peer_data):
        print(f"[PRIMARY] Heartbeat from secondary: block {peer_data.get('last_block')}")

    # Callbacks for secondary
    def on_secondary_register_ack(peer_address, peer_data):
        print(f"[SECONDARY] Registration acknowledged by primary")

    def on_secondary_heartbeat(peer_data):
        print(f"[SECONDARY] Heartbeat from primary: block {peer_data.get('last_block')}")

    # Create primary instance (listens on 9999)
    primary = RedundancyProtocol(
        listen_port=9999,
        auth_token="test-secret-123",
        instance_id="primary-test",
        on_register=on_primary_register,
        on_heartbeat=on_primary_heartbeat
    )
    primary.start()

    # Create secondary instance (listens on 9998)
    secondary = RedundancyProtocol(
        listen_port=9998,
        auth_token="test-secret-123",
        instance_id="secondary-test",
        on_heartbeat=on_secondary_heartbeat
    )
    secondary.start()

    # Wait for threads to start
    time.sleep(1)

    # Secondary registers with primary
    print("\n=== Testing Registration ===")
    secondary.register_with_peer("127.0.0.1", 9999, 9998)

    # Wait for registration
    time.sleep(2)

    # Test heartbeats
    print("\n=== Testing Heartbeats ===")
    for i in range(5):
        primary.send_heartbeat("primary", 1000 + i)
        secondary.send_heartbeat("secondary", 1000 + i)
        time.sleep(1)

    # Check health status
    print(f"\n=== Health Check ===")
    print(f"Primary sees secondary healthy: {primary.is_peer_healthy()}")
    print(f"Secondary sees primary healthy: {secondary.is_peer_healthy()}")

    # Cleanup
    primary.stop()
    secondary.stop()

    print("\n=== Test Complete ===")

if __name__ == "__main__":
    test_protocol()

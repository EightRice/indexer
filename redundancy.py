"""
UDP-based redundancy protocol for indexer instances.

Implements heartbeat and registration protocol between primary and secondary indexers.
"""

import json
import socket
import threading
import time
from typing import Callable, Optional, Dict, Any
from datetime import datetime


# Message types
MSG_REGISTER = "REGISTER"
MSG_REGISTER_ACK = "REGISTER_ACK"
MSG_HEARTBEAT = "HEARTBEAT"
MSG_HEARTBEAT_ACK = "HEARTBEAT_ACK"

# Timing constants
HEARTBEAT_INTERVAL = 5  # seconds
HEARTBEAT_TIMEOUT = 30  # seconds - no heartbeat = peer down
MAX_MESSAGE_SIZE = 4096  # bytes
TIMESTAMP_TOLERANCE = 60  # seconds - reject messages with timestamp off by more than this


class RedundancyProtocol:
    """Manages UDP communication between indexer instances."""

    def __init__(
        self,
        listen_port: int,
        auth_token: str,
        instance_id: str,
        on_register: Optional[Callable] = None,
        on_heartbeat: Optional[Callable] = None,
        on_peer_failure: Optional[Callable] = None
    ):
        """
        Initialize the redundancy protocol.

        Args:
            listen_port: UDP port to listen on
            auth_token: Shared secret for authentication
            instance_id: Unique identifier for this instance
            on_register: Callback when peer registers (peer_address, peer_data)
            on_heartbeat: Callback when heartbeat received (peer_data)
            on_peer_failure: Callback when peer fails (no heartbeat)
        """
        self.listen_port = listen_port
        self.auth_token = auth_token
        self.instance_id = instance_id

        # Callbacks
        self.on_register = on_register
        self.on_heartbeat = on_heartbeat
        self.on_peer_failure = on_peer_failure

        # State
        self.peer_address: Optional[tuple] = None  # (host, port)
        self.last_heartbeat_received = 0
        self.last_heartbeat_sent = 0
        self.running = False

        # Socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', listen_port))

        # Threads
        self.listener_thread: Optional[threading.Thread] = None
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None

    def start(self):
        """Start the redundancy protocol (listener, heartbeat, monitor threads)."""
        if self.running:
            return

        self.running = True

        # Start listener thread
        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listener_thread.start()

        # Start heartbeat sender thread
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

        # Start peer monitor thread
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

        print(f"Redundancy protocol started on port {self.listen_port}")

    def stop(self):
        """Stop the redundancy protocol."""
        self.running = False
        if self.sock:
            self.sock.close()

    def register_with_peer(self, peer_host: str, peer_port: int, my_listen_port: int):
        """
        Send registration message to peer.

        Args:
            peer_host: Peer's IP/hostname
            peer_port: Peer's UDP port
            my_listen_port: This instance's listen port
        """
        self.peer_address = (peer_host, peer_port)

        message = {
            "type": MSG_REGISTER,
            "instance_id": self.instance_id,
            "listen_port": my_listen_port,
            "auth_token": self.auth_token,
            "timestamp": int(time.time())
        }

        self._send_message(message, self.peer_address)
        print(f"Sent REGISTER to {peer_host}:{peer_port}")

    def send_heartbeat(self, role: str, last_block: int):
        """
        Send heartbeat to peer.

        Args:
            role: Current role (primary/secondary)
            last_block: Last processed block number
        """
        if not self.peer_address:
            return  # No peer registered yet

        message = {
            "type": MSG_HEARTBEAT,
            "instance_id": self.instance_id,
            "role": role,
            "last_block": last_block,
            "timestamp": int(time.time())
        }

        self._send_message(message, self.peer_address)
        self.last_heartbeat_sent = int(time.time())

    def _send_message(self, message: Dict[str, Any], address: tuple):
        """Send a JSON message via UDP."""
        try:
            data = json.dumps(message).encode('utf-8')
            self.sock.sendto(data, address)
        except Exception as e:
            print(f"Error sending message: {e}")

    def _listen_loop(self):
        """Background thread that listens for incoming UDP messages."""
        while self.running:
            try:
                data, address = self.sock.recvfrom(MAX_MESSAGE_SIZE)
                message = json.loads(data.decode('utf-8'))

                # Validate timestamp
                msg_time = message.get('timestamp', 0)
                current_time = int(time.time())

                if abs(current_time - msg_time) > TIMESTAMP_TOLERANCE:
                    print(f"Rejected message with stale timestamp from {address}")
                    continue

                # Route to handler
                msg_type = message.get('type')

                if msg_type == MSG_REGISTER:
                    self._handle_register(message, address)
                elif msg_type == MSG_REGISTER_ACK:
                    self._handle_register_ack(message, address)
                elif msg_type == MSG_HEARTBEAT:
                    self._handle_heartbeat(message, address)
                elif msg_type == MSG_HEARTBEAT_ACK:
                    self._handle_heartbeat_ack(message, address)
                else:
                    print(f"Unknown message type: {msg_type}")

            except socket.timeout:
                continue
            except json.JSONDecodeError:
                print(f"Received malformed JSON from {address}")
            except Exception as e:
                if self.running:  # Only log if not shutting down
                    print(f"Error in listener loop: {e}")

    def _handle_register(self, message: Dict[str, Any], address: tuple):
        """Handle REGISTER message from peer."""
        # Validate auth token
        if message.get('auth_token') != self.auth_token:
            print(f"REGISTER rejected: invalid auth token from {address}")
            return

        # Extract peer info
        peer_instance_id = message.get('instance_id')
        peer_listen_port = message.get('listen_port')

        # Store peer address (use peer's listen port, not source port)
        peer_host = address[0]
        self.peer_address = (peer_host, peer_listen_port)

        print(f"Peer registered: {peer_instance_id} at {peer_host}:{peer_listen_port}")

        # Send ACK
        ack_message = {
            "type": MSG_REGISTER_ACK,
            "instance_id": self.instance_id,
            "status": "ok",
            "timestamp": int(time.time())
        }
        self._send_message(ack_message, self.peer_address)

        # Call callback
        if self.on_register:
            self.on_register(self.peer_address, message)

        # Mark as alive
        self.last_heartbeat_received = int(time.time())

    def _handle_register_ack(self, message: Dict[str, Any], address: tuple):
        """Handle REGISTER_ACK message from peer."""
        print(f"Registration acknowledged by {address}")
        self.last_heartbeat_received = int(time.time())

    def _handle_heartbeat(self, message: Dict[str, Any], address: tuple):
        """Handle HEARTBEAT message from peer."""
        self.last_heartbeat_received = int(time.time())

        # Send ACK (optional)
        ack_message = {
            "type": MSG_HEARTBEAT_ACK,
            "instance_id": self.instance_id,
            "timestamp": int(time.time())
        }
        self._send_message(ack_message, address)

        # Call callback
        if self.on_heartbeat:
            self.on_heartbeat(message)

    def _handle_heartbeat_ack(self, message: Dict[str, Any], address: tuple):
        """Handle HEARTBEAT_ACK message from peer."""
        # Just note that peer is alive
        pass

    def _heartbeat_loop(self):
        """Background thread that sends periodic heartbeats."""
        # Wait a bit before starting heartbeats (let registration happen first)
        time.sleep(2)

        while self.running:
            # Heartbeats are sent by main app (which has role/block info)
            # This thread just enforces timing
            time.sleep(HEARTBEAT_INTERVAL)

    def _monitor_loop(self):
        """Background thread that monitors peer health."""
        while self.running:
            time.sleep(5)  # Check every 5 seconds

            if not self.peer_address:
                continue  # No peer registered yet

            time_since_heartbeat = int(time.time()) - self.last_heartbeat_received

            if time_since_heartbeat > HEARTBEAT_TIMEOUT:
                # Peer is down!
                if self.on_peer_failure:
                    print(f"Peer failure detected: no heartbeat for {time_since_heartbeat}s")
                    self.on_peer_failure()
                    # Only call once, then wait for peer to come back
                    self.peer_address = None

    def get_time_since_last_heartbeat(self) -> int:
        """Get seconds since last heartbeat was received."""
        return int(time.time()) - self.last_heartbeat_received

    def is_peer_healthy(self) -> bool:
        """Check if peer is currently healthy (recent heartbeat)."""
        return self.get_time_since_last_heartbeat() < HEARTBEAT_TIMEOUT

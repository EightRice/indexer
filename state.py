"""
Local state management for indexer redundancy.

Stores persistent state in SQLite for tracking:
- Current role (primary/secondary)
- Peer address and status
- Last processed block
- Instance metadata
"""

import sqlite3
import threading
import time
from typing import Optional


class IndexerState:
    """Thread-safe SQLite state management."""

    def __init__(self, db_path: str = "indexer_state.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at INTEGER
                )
            """)

            conn.commit()
            conn.close()

    def set(self, key: str, value: str) -> None:
        """Set a state value."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO state (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, int(time.time())))

            conn.commit()
            conn.close()

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a state value."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT value FROM state WHERE key = ?", (key,))
            result = cursor.fetchone()

            conn.close()

            return result[0] if result else default

    def get_int(self, key: str, default: int = 0) -> int:
        """Get a state value as integer."""
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def get_timestamp(self, key: str) -> Optional[int]:
        """Get the timestamp when a key was last updated."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT updated_at FROM state WHERE key = ?", (key,))
            result = cursor.fetchone()

            conn.close()

            return result[0] if result else None

    def delete(self, key: str) -> None:
        """Delete a state key."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("DELETE FROM state WHERE key = ?", (key,))

            conn.commit()
            conn.close()

    def get_all(self) -> dict:
        """Get all state as a dictionary."""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT key, value, updated_at FROM state")
            results = cursor.fetchall()

            conn.close()

            return {
                row[0]: {
                    'value': row[1],
                    'updated_at': row[2]
                }
                for row in results
            }

    # Convenience methods for specific state keys

    def get_current_role(self) -> Optional[str]:
        """Get current role (primary or secondary)."""
        return self.get('current_role')

    def set_current_role(self, role: str) -> None:
        """Set current role."""
        if role not in ['primary', 'secondary']:
            raise ValueError(f"Invalid role: {role}")
        self.set('current_role', role)

    def get_peer_address(self) -> Optional[str]:
        """Get peer address (host:port)."""
        return self.get('peer_address')

    def set_peer_address(self, address: str) -> None:
        """Set peer address."""
        self.set('peer_address', address)

    def get_last_processed_block(self) -> int:
        """Get last processed block number."""
        return self.get_int('last_processed_block', 0)

    def set_last_processed_block(self, block_number: int) -> None:
        """Set last processed block number."""
        self.set('last_processed_block', str(block_number))

    def get_instance_id(self) -> Optional[str]:
        """Get instance ID."""
        return self.get('instance_id')

    def set_instance_id(self, instance_id: str) -> None:
        """Set instance ID."""
        self.set('instance_id', instance_id)

    def get_started_as(self) -> Optional[str]:
        """Get the role this instance started as."""
        return self.get('started_as')

    def set_started_as(self, role: str) -> None:
        """Set the role this instance started as."""
        if role not in ['primary', 'secondary']:
            raise ValueError(f"Invalid role: {role}")
        self.set('started_as', role)

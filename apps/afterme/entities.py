# indexer/apps/afterme/entities.py
from datetime import datetime, timezone

class Will:
    def __init__(self, address, owner, interval, last_update_timestamp, executed, has_diary, status):
        self.address = address
        self.owner = owner
        self.interval = interval
        self.last_update = datetime.fromtimestamp(last_update_timestamp, tz=timezone.utc)
        self.executed = executed
        self.hasDiary = has_diary
        self.status = status # 'Empty', 'Active', 'Executed'
        self.created_at = datetime.now(timezone.utc)

    def to_firestore(self):
        """Serializes the object to a dictionary for Firestore."""
        return {
            'address': self.address,
            'owner': self.owner,
            'interval': self.interval, # in seconds
            'lastUpdate': self.last_update, # as a datetime object
            'executed': self.executed,
            'hasDiary': self.hasDiary,
            'status': self.status,
            'createdAt': self.created_at,
            'lastIndexed': datetime.now(timezone.utc)
        }
# indexer/apps/afterme/entities.py
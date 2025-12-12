# Indexer Redundancy System

## Overview

The indexer now supports active-passive redundancy with automatic failover. Two instances can run simultaneously - a primary that writes to Firestore and a secondary that monitors events but only writes if the primary fails.

## Architecture

- **UDP Heartbeat Protocol**: Direct peer-to-peer communication for health monitoring
- **SQLite State Management**: Local persistent state tracking (role, peer info, last processed block)
- **Automatic Role Switching**: Instances adapt their role based on reality ("digital weather")
- **Two-Step Failover Confirmation**: Requires both UDP timeout AND stale Firestore checkpoint
- **Discord Alerts**: Notifications for all redundancy events

## Files Added

1. **`state.py`** - Thread-safe SQLite state management
2. **`redundancy.py`** - UDP protocol implementation (REGISTER, HEARTBEAT, ACK messages)
3. **`test_redundancy.py`** - Unit test for UDP protocol
4. **`test_integration.py`** - Integration test script (Windows path issues - use manual testing)

## Command-Line Arguments

```bash
--mode {primary|secondary|standalone}
    # primary: Active indexer (writes to Firestore)
    # secondary: Passive monitor (only writes if primary fails)
    # standalone: No redundancy (default)

--listen-port PORT
    # UDP port for this instance (default: 9999)

--peer-address HOST:PORT
    # Address of peer instance (required for secondary)

--instance-id ID
    # Unique identifier (auto-generated if not provided)
```

## Environment Variables

```bash
REDUNDANCY_AUTH_TOKEN=your-secret-token-here
```

**IMPORTANT**: Set this environment variable for production! Without it, a default token is used (insecure).

## Usage Examples

### Starting Primary Instance

```bash
export REDUNDANCY_AUTH_TOKEN="my-secret-token-123"

python app.py localhost homebase \
  --mode primary \
  --listen-port 9999 \
  --instance-id primary-indexer-1
```

### Starting Secondary Instance

```bash
export REDUNDANCY_AUTH_TOKEN="my-secret-token-123"

python app.py localhost homebase \
  --mode secondary \
  --listen-port 9998 \
  --peer-address 127.0.0.1:9999 \
  --instance-id secondary-indexer-1
```

## How It Works

### 1. Startup Role Detection

When an instance starts, it compares:
- **Local SQLite block**: Last block this instance processed
- **Firestore block**: Last block written to Firestore

If Firestore is ahead by >5 blocks:
- Instance was configured as PRIMARY → Automatically becomes SECONDARY
- Another instance must have taken over while this one was down
- Discord alert sent: "PRIMARY → SECONDARY: Another instance took over"

### 2. Heartbeat Protocol

- **Interval**: 5 seconds
- **Timeout**: 30 seconds (no heartbeat = peer down)
- **Messages**: REGISTER, REGISTER_ACK, HEARTBEAT, HEARTBEAT_ACK
- **Authentication**: Shared token validates peer registration
- **Timestamp Validation**: Rejects messages with timestamps off by >60 seconds

### 3. Failover Process

When secondary detects primary failure:

1. **UDP Timeout**: No heartbeat for 30+ seconds
2. **Firestore Confirmation**: Checks if `lastSyncedBlock` is stale
3. **Role Promotion**: SECONDARY → PRIMARY
4. **Alert Sent**: "FAILOVER: Secondary promoted to PRIMARY"
5. **Firestore Writes**: Secondary starts writing events

### 4. Recovery Behavior

When original primary recovers:

1. **Startup Check**: Compares local SQLite vs Firestore
2. **Detects Firestore Ahead**: Realizes another instance took over
3. **Auto-Demotion**: PRIMARY → SECONDARY
4. **Peer Registration**: Registers with current primary (using stored peer address)
5. **Alert Sent**: "Role changed: PRIMARY → SECONDARY"

## Testing

### 1. UDP Protocol Test

```bash
cd indexer
python test_redundancy.py
```

Expected output:
```
=== Testing Registration ===
[PRIMARY] Peer registered: ('127.0.0.1', 9998)
[SECONDARY] Registration acknowledged by primary

=== Testing Heartbeats ===
[PRIMARY] Heartbeat from secondary: block 1000
[SECONDARY] Heartbeat from primary: block 1000
...

=== Health Check ===
Primary sees secondary healthy: True
Secondary sees primary healthy: True
```

### 2. Manual Integration Test

**Step 1: Clean state**
```bash
cd indexer
rm -f indexer_state_localhost.db *.log
# Also clear Firestore data in emulator UI
```

**Step 2: Start primary**
```bash
python -u app.py localhost homebase \
  --mode primary \
  --listen-port 9999 \
  --instance-id primary-test
```

Verify output shows:
- "Redundancy protocol started on port 9999"
- "Redundancy Enabled: Role=PRIMARY"

**Step 3: Start secondary (new terminal)**
```bash
python -u app.py localhost homebase \
  --mode secondary \
  --listen-port 9998 \
  --peer-address 127.0.0.1:9999 \
  --instance-id secondary-test
```

Verify output shows:
- "Redundancy protocol started on port 9998"
- "Registering with primary at 127.0.0.1:9999"
- "Redundancy Enabled: Role=SECONDARY"

**Step 4: Monitor heartbeats**

Both instances should exchange heartbeats every 5 seconds.

**Step 5: Simulate primary failure**

Kill the primary process (Ctrl+C or `kill`). Watch secondary output:
- After ~30 seconds: "Peer failure detected"
- "SECONDARY → PRIMARY: Promoting to active indexer"
- Discord alert sent
- Secondary starts writing to Firestore

**Step 6: Test recovery**

Restart the original primary. It should:
- Detect Firestore ahead
- Auto-demote to SECONDARY
- Register with current primary (original secondary)

## Configuration Constants

In `redundancy.py`:

```python
HEARTBEAT_INTERVAL = 5  # seconds between heartbeats
HEARTBEAT_TIMEOUT = 30  # seconds before peer considered down
TIMESTAMP_TOLERANCE = 60  # reject messages with old timestamps
```

## Security Considerations

1. **Authentication**: Shared token prevents unauthorized peer registration
2. **Timestamp Validation**: Prevents replay attacks
3. **Local UDP Only**: Not designed for internet routing (use VPN for remote peers)
4. **Firestore Rules**: Ensure only authorized instances can write

## Firestore Checkpoint Updates

- **Before**: Only updated after historical sync completion
- **Now**: Updated after EVERY event batch processed
- **Benefit**: Secondary can detect primary failure within seconds

## SQLite State Schema

```sql
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER
);
```

**Keys used**:
- `current_role`: primary or secondary
- `peer_address`: host:port of peer instance
- `last_processed_block`: last block number processed
- `instance_id`: unique identifier for this instance
- `started_as`: original role at startup

## Discord Alerts

Redundancy events trigger Discord notifications:

- "🔄 Role changed: PRIMARY → SECONDARY" (startup detection)
- "🚨 FAILOVER: Secondary promoted to PRIMARY" (peer failure)
- "⚠️ Failed to register with peer" (registration error)

## Troubleshooting

### "WARNING: Using default auth token"

Set the `REDUNDANCY_AUTH_TOKEN` environment variable before starting instances.

### Secondary doesn't promote after primary failure

Check:
1. UDP heartbeat timeout (30 seconds)
2. Firestore `lastSyncedBlock` is being updated
3. Secondary has `on_peer_failure` callback configured

### "Firestore ahead" message on startup

This is normal if:
- Another instance was running while this one was down
- Previous test wrote to Firestore without clearing state

**Solution**: Clear both Firestore and SQLite state for fresh start.

### UDP messages not received

Check:
1. Firewall allows UDP on specified ports
2. Both instances using same auth token
3. Peer address format is correct (host:port)

## Production Deployment

1. **Set auth token**: Export `REDUNDANCY_AUTH_TOKEN` environment variable
2. **Configure peer addresses**: Use physical IPs (not in Firestore)
3. **VPN recommended**: For remote peer communication
4. **Monitor alerts**: Set up Discord webhook for critical notifications
5. **Test failover**: Regularly verify failover works as expected

## Next Steps

- [ ] Set up VPN tunnel for remote instance communication
- [ ] Configure production auth token
- [ ] Set up monitoring/alerting for failover events
- [ ] Test with real network conditions (latency, packet loss)
- [ ] Document recovery procedures for operations team

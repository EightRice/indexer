# Historical Lookup Implementation

## Overview

Both AfterMe and Homebase now have historical lookup capabilities that allow the indexer to recover from downtime by scanning missed events.

## AfterMe Historical Lookup

**Approach**: Event scan + view function reconciliation

**Process**:
1. Scans `WillCreated` and `WillCleared` events from `fromBlock` to latest
2. Reconciles events to determine which wills should exist
3. Calls `get_onchain_will_data()` view function for each will
4. Updates Firestore with current on-chain state
5. Deletes wills that were cleared

**Advantage**: Always has accurate current state from blockchain
**Location**: `app.py` lines 192-251

## Homebase Historical Lookup

**Approach**: Event-only rebuilding (no view functions)

**Process**:
1. For each DAO in Firestore, scans all governance events:
   - `DelegateChanged` (on token contract)
   - `ProposalCreated` (on DAO contract)
   - `VoteCast` (on DAO contract)
   - `ProposalQueued` (on DAO contract)
   - `ProposalExecuted` (on DAO contract)
2. Sorts events chronologically by block, transaction index, log index
3. Processes events in order to rebuild state
4. Checks Firestore for existing data to avoid duplicates
5. Only processes missing events

**Why event-only?**
The Homebase contracts don't expose view functions to query full DAO state like:
- All proposals and their complete data
- All votes on all proposals
- All delegation relationships

**Location**: `app.py` lines 180-369

## Configuration

Both systems use the `networks/{network}` Firestore document:

```javascript
{
  "rpc": "https://node.mainnet.etherlink.com",
  "fromBlock": 0,           // Start scanning from this block
  "lastSyncedBlock": 12345  // Checkpoint for resumption
}
```

### Safety Margin

Both use a `REORG_SAFETY_MARGIN` of 100 blocks:
```python
start_block = max(from_block_config, last_synced_block - REORG_SAFETY_MARGIN)
```

This handles blockchain reorganizations by rescanning recent blocks.

## When Historical Sync Runs

**Startup**: Every time the indexer starts, it runs historical sync before entering real-time monitoring mode.

**For Homebase**:
- Only syncs DAOs that already exist in Firestore
- Does not discover new DAOs (NewDaoCreated events are handled in real-time)
- Fills in any missing proposals, votes, or delegation changes

**For AfterMe**:
- Scans all wills from the source contract
- Adds new wills, updates existing ones, deletes cleared ones
- Full reconciliation with on-chain state

## Performance Considerations

### Chunk Sizing
Both implementations use adaptive chunk sizing:
- Start with 1000 block chunks
- If RPC returns "block range too large" error, reduce chunk size by half
- Retry with smaller chunks

### Rate Limiting
- Homebase: 0.1s sleep between chunks
- AfterMe: 0.2s sleep between chunks

### Event Deduplication

**Homebase checks**:
- ProposalCreated: Skip if proposal ID already exists in Firestore
- VoteCast: Skip if voter already has a vote document for that proposal
- Other events: Always process (they update state)

**AfterMe**:
- Full reconciliation, so duplicates are handled by merge logic

## Testing

To test historical lookup on localhost:

1. Start services:
```bash
cd homebase-evm-contracts && npx hardhat node
cd indexer && firebase emulators:start --only firestore
```

2. Deploy contracts and create some governance activity

3. Stop the indexer

4. Create more governance activity while indexer is down

5. Restart indexer - it should catch up:
```bash
cd indexer && python app.py localhost homebase
```

You should see output like:
```
--- Starting Homebase Historical Sync ---
Checkpoint found: 50. Applying safety margin, starting from 0.
Scanning for Homebase events from block 0 to 75...
Found 1 DAOs in Firestore to sync.

Syncing DAO: 0x68B1D87F95878fE05B998F19b66F4baba5De1aed
  Scanning events from block 0 to 75...
  Found 12 events to process
  Existing in Firestore: 1 proposals, 1 members
  Processed 11 new/updated events for DAO 0x68B1D87F95878fE05B998F19b66F4baba5De1aed

--- Homebase Historical Sync Complete --- Checkpoint updated to block 75.
```

## Future Improvements

### For Homebase:
1. **Discover new DAOs**: Scan for `NewDaoCreated` events during historical sync
2. **Member balance tracking**: Track `Transfer` events to update member balances
3. **Proposal status reconciliation**: Query contract for current proposal state

### For Both:
1. **Parallel scanning**: Scan multiple DAOs/contracts concurrently
2. **Progress tracking**: Store intermediate checkpoints for very long syncs
3. **Selective sync**: Only sync specific DAOs or date ranges on demand

# Real Network Testing Plan

## Available Networks & Accounts

### Etherlink Testnet (Ghostnet)
- **RPC**: https://node.ghostnet.etherlink.com
- **Chain ID**: 128123
- **Accounts** (all have balance):
  - ACCOUNT1: `0x06e5b15bc39f921e1503073dbb8a5da2fc6220e9` (main funder)
  - ALICE: `0x6e147e1d239bf49c88d64505e746e8522845d8d3`
  - BOB: `0x6a9cbf5d01b9760ca99c3c27db0b23e3b8bd454b`
  - TIM: `0x530ef26c437d424e08839903189b1fafb2b14a27`
  - SAM: `0x3057d3a1db6b8a3ae50d3b093647c60e58f77e39`

### Etherlink Mainnet
- **RPC**: https://node.mainnet.etherlink.com
- **Chain ID**: 42793
- **Note**: ACCOUNT1 has balance, can transfer to others as needed

---

## Test Scenarios

### Phase 1: Etherlink Testnet Testing (SAFER - DO THIS FIRST)

#### 1.1 - Historical Sync Test
**Goal**: Verify historical lookup works with real on-chain data

**Steps**:
1. Check Firestore for existing DAOs on testnet
2. Note the current `lastSyncedBlock` in Firestore
3. Delete `lastSyncedBlock` (or set to much earlier block)
4. Start indexer in standalone mode
5. Verify it catches up and processes all historical events
6. Confirm DAO data matches blockchain state

#### 1.2 - Real-Time Event Processing
**Goal**: Verify new events are captured correctly

**Steps**:
1. Create a new proposal using ACCOUNT1
2. Vote on the proposal using ALICE and BOB
3. Verify indexer captures all events
4. Check Firestore has correct proposal and vote data
5. Verify Discord alerts (if configured)

#### 1.3 - Redundancy: Single Instance Failover Simulation
**Goal**: Test role detection and state management

**Steps**:
1. Start indexer as PRIMARY
2. Stop indexer
3. Create some proposals while down
4. Restart same indexer as PRIMARY
5. Verify it detects it's behind and auto-demotes to SECONDARY
6. After catching up, manually promote back to PRIMARY

#### 1.4 - Redundancy: Two Instance Failover (ADVANCED)
**Goal**: Test real failover between two machines/processes

**Prerequisites**:
- Two machines with public IPs (or same machine, different ports)
- Configure `REDUNDANCY_AUTH_TOKEN` environment variable
- Firewall rules allow UDP on chosen ports

**Steps**:
1. Start indexer #1 as PRIMARY on port 9999
2. Start indexer #2 as SECONDARY on port 9998, peer = PRIMARY's IP:9999
3. Verify heartbeats exchanging (check logs)
4. Create a proposal - verify PRIMARY writes it
5. Kill PRIMARY
6. Wait 30+ seconds
7. Verify SECONDARY promotes to PRIMARY
8. Create another proposal - verify NEW primary writes it
9. Restart original PRIMARY
10. Verify it detects it's behind and becomes SECONDARY

---

### Phase 2: Etherlink Mainnet Testing (AFTER TESTNET SUCCESS)

#### 2.1 - Read-Only Historical Sync
**Goal**: Verify indexer works with production data WITHOUT modifying Firestore

**Steps**:
1. Run indexer in **dry-run mode** (we'll need to add this flag)
2. Connect to production Firestore in READ-ONLY mode
3. Verify it can scan and process existing DAOs
4. Check for any errors or edge cases

#### 2.2 - Controlled Redundancy Test
**Goal**: Test redundancy on mainnet with minimal risk

**Steps**:
1. Start indexer as SECONDARY (monitoring only)
2. Let it run for 1 hour monitoring existing primary
3. Verify it tracks all events correctly
4. No failover - just monitoring

---

## Pre-Flight Checklist

### Environment Setup

- [ ] Firestore credentials configured
- [ ] Discord webhook URL set (for alerts)
- [ ] `REDUNDANCY_AUTH_TOKEN` environment variable set
- [ ] Python dependencies installed

### Network Configuration Files

Create these config files:

**`indexer/network_info/etherlink-testnet_homebase.json`**:
```json
{
  "homebase_dao_address": "0x...",
  "start_block": 0
}
```

**`indexer/network_info/etherlink-mainnet_homebase.json`**:
```json
{
  "homebase_dao_address": "0x...",
  "start_block": 0
}
```

### Firestore Collections

Verify these exist for each network:
- `homebase/{network}/daos/`
- `contracts/{network}`
- `networks/{network}`

---

## Safety Measures

### Before Running on Mainnet:

1. **Backup Firestore Data**
   ```bash
   gcloud firestore export gs://your-bucket/backup-$(date +%Y%m%d)
   ```

2. **Test Mode Flag**
   Add a `--dry-run` flag that:
   - Reads from Firestore normally
   - Processes all events
   - **DOES NOT write to Firestore**
   - Logs what it would have written

3. **Rate Limiting**
   - Add delays between RPC calls (already have this)
   - Monitor RPC usage

4. **Monitoring**
   - Watch Discord for alerts
   - Check logs for errors
   - Monitor Firestore writes

---

## Troubleshooting

### "Node connection failed"
- Check RPC URL is correct
- Verify network/internet connectivity
- Try alternative RPC endpoint

### "No events found"
- Verify contract addresses are correct
- Check start_block is set correctly
- Ensure contracts are actually deployed on this network

### "Firestore permission denied"
- Check Firebase credentials
- Verify service account has correct permissions
- Ensure Firestore rules allow writes

### "UDP connection failed" (redundancy)
- Check firewall rules
- Verify ports are open
- Test with `netstat` or `telnet`

---

## Success Criteria

### Historical Sync ✓
- [ ] All existing DAOs loaded from blockchain
- [ ] Proposal counts match on-chain data
- [ ] Vote records accurate
- [ ] No errors in logs

### Real-Time Indexing ✓
- [ ] New proposals captured within 10 seconds
- [ ] Votes recorded correctly
- [ ] Discord alerts sent for key events
- [ ] `lastSyncedBlock` updates correctly

### Redundancy ✓
- [ ] Heartbeats exchange successfully
- [ ] Failover happens within 35 seconds
- [ ] Secondary promotes cleanly
- [ ] Recovered primary demotes correctly
- [ ] No data loss during failover
- [ ] Discord alerts for all role changes

---

## Next Steps After Testing

1. Document any issues found
2. Fix bugs if any
3. Create runbook for production deployment
4. Set up monitoring/alerting
5. Create backup/recovery procedures
6. Deploy to production!

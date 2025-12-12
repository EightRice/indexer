# Etherlink Testnet Testing - Quick Start Guide

## Prerequisites

1. **Firebase Credentials**
   ```bash
   export FIREBASE_CREDENTIALS=/path/to/your-service-account.json
   ```

2. **Discord Webhook (Optional)**
   ```bash
   export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```

3. **Redundancy Auth Token (For redundancy tests)**
   ```bash
   export REDUNDANCY_AUTH_TOKEN="your-secret-token-here"
   ```

## Step 1: Verify Firestore Configuration

Your production Firestore should already have:

### Required Documents

**`contracts/etherlink-testnet`**:
```json
{
  "wrapper_jurisdiction": "0x...",
  "wrapper_w": "0x..." // If you have wrapped token support
}
```

**`networks/etherlink-testnet`**:
```json
{
  "rpc": "https://node.ghostnet.etherlink.com",
  "fromBlock": 0,
  "lastSyncedBlock": <current_block>
}
```

### Check These Exist

```bash
# Install Firebase CLI if needed
npm install -g firebase-tools

# Login
firebase login

# Check Firestore data
firebase firestore:get contracts/etherlink-testnet
firebase firestore:get networks/etherlink-testnet
```

## Step 2: Choose Your Test

### Option A: Simple Historical Sync Test

**Goal**: Verify indexer can catch up with blockchain

```bash
cd indexer
chmod +x test_etherlink_testnet.sh
./test_etherlink_testnet.sh
# Select option 1 (Historical sync test)
# Enter starting block (e.g., 100 blocks ago)
```

**What to watch for**:
- Indexer connects to RPC ✓
- Historical sync starts ✓
- Events are processed ✓
- DAOs appear in Firestore ✓
- Checkpoint updates ✓

### Option B: Real-Time Monitoring

**Goal**: Verify indexer captures new events

```bash
./test_etherlink_testnet.sh
# Select option 2 (Real-time indexing)
```

Then create a test proposal:
```bash
cd ../homebase-evm-contracts

# Create a test proposal on testnet
npx hardhat run scripts/testGovernance.js --network etherlink-testnet
```

**What to watch for**:
- New proposal appears in indexer logs ✓
- Proposal written to Firestore ✓
- Discord alert sent (if configured) ✓

### Option C: Redundancy Test (Single Machine)

**Goal**: Test failover simulation

```bash
# Terminal 1 - Start as PRIMARY
./test_etherlink_testnet.sh
# Select option 4 (PRIMARY with failover)
# Port: 9999

# Terminal 2 - Start as SECONDARY
./test_etherlink_testnet.sh
# Select option 5 (SECONDARY mode)
# Port: 9998
# Primary address: 127.0.0.1:9999
```

**Test failover**:
1. Watch both terminals - heartbeats should exchange
2. Kill Terminal 1 (Ctrl+C)
3. Wait 30+ seconds
4. Terminal 2 should promote to PRIMARY
5. Create a test proposal (should be indexed by new PRIMARY)
6. Restart Terminal 1 - should detect it's behind and become SECONDARY

## Step 3: Monitor & Verify

### Check Logs

```bash
# Indexer should show:
# - Connected to RPC
# - Redundancy protocol started (if redundancy mode)
# - Events being processed
# - Checkpoint updates
```

### Check Firestore

```bash
# View DAOs
firebase firestore:get homebase/etherlink-testnet/daos

# Check latest checkpoint
firebase firestore:get networks/etherlink-testnet
```

### Check Discord

If webhook configured, you should see alerts for:
- Redundancy role changes
- Important errors

## Common Issues

### "FIREBASE_CREDENTIALS not found"

```bash
# Set the environment variable
export FIREBASE_CREDENTIALS=/path/to/credentials.json

# Or hardcode in Python (NOT recommended for production)
# Edit indexer/app.py line ~75
```

### "Connection refused" (UDP redundancy)

```bash
# Check firewall
sudo ufw allow 9999/udp
sudo ufw allow 9998/udp

# Or test locally first (127.0.0.1)
```

### "No contract addresses found"

Your Firestore `contracts/etherlink-testnet` document is missing or incorrect.

**Option 1**: Check production Firestore
```bash
firebase firestore:get contracts/etherlink-testnet
```

**Option 2**: If you don't have existing contracts, deploy new ones:
```bash
cd ../homebase-evm-contracts
npx hardhat run scripts/deploy.js --network etherlink-testnet

# Then update Firestore with new addresses
```

### "Historical sync finds no events"

This is OK if:
- No DAOs exist on this network yet
- `fromBlock` is set too high

Create a test DAO:
```bash
cd ../homebase-evm-contracts
npx hardhat run scripts/testGovernance.js --network etherlink-testnet
```

## Network Endpoints

### Etherlink Testnet
- **RPC**: https://node.ghostnet.etherlink.com
- **Chain ID**: 128123
- **Explorer**: https://testnet.explorer.etherlink.com

### Etherlink Mainnet
- **RPC**: https://node.mainnet.etherlink.com
- **Chain ID**: 42793
- **Explorer**: https://explorer.etherlink.com

## Account Balances

Check account balances:

```javascript
// In hardhat console
npx hardhat console --network etherlink-testnet

const balance = await ethers.provider.getBalance("0x06e5b15bc39f921e1503073dbb8a5da2fc6220e9");
console.log(ethers.formatEther(balance));
```

Transfer funds if needed:

```javascript
const [signer] = await ethers.getSigners();
const tx = await signer.sendTransaction({
  to: "0x6e147e1d239bf49c88d64505e746e8522845d8d3", // ALICE
  value: ethers.parseEther("1.0")
});
await tx.wait();
```

## Success Checklist

- [ ] Indexer connects to Etherlink Testnet RPC
- [ ] Historical sync completes without errors
- [ ] New proposals are captured in real-time
- [ ] Firestore data matches blockchain state
- [ ] Discord alerts working (if configured)
- [ ] Redundancy heartbeats exchange (if testing redundancy)
- [ ] Failover works correctly (if testing redundancy)
- [ ] Recovered instance auto-demotes (if testing redundancy)

## Next Steps

After successful testnet testing:

1. Review any errors or issues
2. Fix bugs if found
3. Test on Etherlink Mainnet (CAREFULLY!)
4. Document production deployment procedure
5. Deploy to production 🚀

## Emergency Stop

If anything goes wrong:

```bash
# Stop indexer
Ctrl+C

# Kill all indexer processes
pkill -f "python.*app.py"

# Check what was written to Firestore
firebase firestore:get homebase/etherlink-testnet/daos --limit 10

# Rollback if needed (restore from backup)
```

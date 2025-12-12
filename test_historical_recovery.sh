#!/bin/bash
# Test script for Homebase historical lookup after downtime

echo "=================================================="
echo "  HISTORICAL LOOKUP RECOVERY TEST"
echo "=================================================="
echo ""

# Ensure we're in the right directory
cd "$(dirname "$0")"

echo "Step 1: Clean up previous test data"
rm -f indexer_state_localhost.db *.log
echo "✓ State cleaned"
echo ""

echo "Step 2: Start indexer in background"
python -u app.py localhost homebase > indexer_phase1.log 2>&1 &
INDEXER_PID=$!
echo "Indexer started with PID: $INDEXER_PID"
sleep 5

# Check if process is running
if ! ps -p $INDEXER_PID > /dev/null; then
    echo "ERROR: Indexer failed to start!"
    cat indexer_phase1.log
    exit 1
fi
echo "✓ Indexer running"
echo ""

echo "Step 3: Create first proposal (while indexer is running)"
cd ../homebase-evm-contracts
npx hardhat run scripts/testGovernance.js --network localhost > /dev/null 2>&1
cd ../indexer
echo "✓ First proposal created"
sleep 3
echo ""

echo "Step 4: Stop indexer (simulating downtime)"
kill $INDEXER_PID
wait $INDEXER_PID 2>/dev/null
echo "✓ Indexer stopped"
echo ""

echo "Step 5: Create second proposal (while indexer is DOWN)"
cd ../homebase-evm-contracts
npx hardhat run scripts/testGovernance.js --network localhost > /dev/null 2>&1
cd ../indexer
echo "✓ Second proposal created (indexer missed this!)"
echo ""

echo "Step 6: Create third proposal (still down)"
cd ../homebase-evm-contracts
npx hardhat run scripts/testGovernance.js --network localhost > /dev/null 2>&1
cd ../indexer
echo "✓ Third proposal created (indexer missed this too!)"
echo ""

echo "Step 7: Restart indexer (should catch up via historical sync)"
echo "Starting indexer in foreground for 10 seconds..."
timeout 10 python -u app.py localhost homebase 2>&1 | tee indexer_phase2.log

echo ""
echo "=================================================="
echo "  ANALYZING RESULTS"
echo "=================================================="
echo ""

echo "Checking for historical sync messages..."
if grep -q "Starting Homebase Historical Sync" indexer_phase2.log; then
    echo "✓ Historical sync started"
else
    echo "✗ Historical sync did NOT start!"
fi

if grep -q "Processed.*new/updated events" indexer_phase2.log; then
    EVENTS_FOUND=$(grep "Processed.*new/updated events" indexer_phase2.log)
    echo "✓ Events processed: $EVENTS_FOUND"
else
    echo "✗ No events processed message found"
fi

if grep -q "Historical Sync Complete" indexer_phase2.log; then
    echo "✓ Historical sync completed successfully"
else
    echo "✗ Historical sync did NOT complete"
fi

echo ""
echo "Full historical sync output:"
grep -A20 "Starting Homebase Historical Sync" indexer_phase2.log || echo "No historical sync found"

echo ""
echo "=================================================="
echo "  TEST COMPLETE"
echo "=================================================="
echo ""
echo "Check indexer_phase1.log for initial run output"
echo "Check indexer_phase2.log for recovery run output"

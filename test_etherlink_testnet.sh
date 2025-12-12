#!/bin/bash
# Quick test script for Etherlink Testnet

set -e

echo "=================================================="
echo "  ETHERLINK TESTNET INDEXER TEST"
echo "=================================================="

# Configuration
NETWORK="etherlink-testnet"
RPC_URL="https://node.ghostnet.etherlink.com"
CHAIN_ID=128123

# Check prerequisites
echo -e "\n[1/5] Checking prerequisites..."

if [ -z "$FIREBASE_CREDENTIALS" ]; then
    echo "⚠️  FIREBASE_CREDENTIALS not set"
    echo "   Set it to your service account JSON file path:"
    echo "   export FIREBASE_CREDENTIALS=/path/to/credentials.json"
fi

if [ -z "$DISCORD_WEBHOOK_URL" ]; then
    echo "⚠️  DISCORD_WEBHOOK_URL not set (alerts will be skipped)"
fi

if [ -z "$REDUNDANCY_AUTH_TOKEN" ]; then
    echo "⚠️  REDUNDANCY_AUTH_TOKEN not set (using default - NOT SECURE!)"
    echo "   Set it for production:"
    echo "   export REDUNDANCY_AUTH_TOKEN=your-secret-token"
fi

echo "✓ Prerequisites checked"

# Check connection to RPC
echo -e "\n[2/5] Testing RPC connection..."
if curl -s -X POST -H "Content-Type: application/json" \
    --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
    $RPC_URL | grep -q "result"; then
    echo "✓ Connected to $RPC_URL"

    # Get current block
    BLOCK=$(curl -s -X POST -H "Content-Type: application/json" \
        --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
        $RPC_URL | grep -o '"result":"[^"]*"' | cut -d'"' -f4)
    BLOCK_DEC=$((16#${BLOCK:2}))
    echo "   Current block: $BLOCK_DEC"
else
    echo "✗ Failed to connect to RPC!"
    exit 1
fi

# Prompt for configuration
echo -e "\n[3/5] Configuration"
echo ""
echo "What do you want to test?"
echo "  1) Historical sync test (test catching up from old block)"
echo "  2) Real-time indexing (monitor new events)"
echo "  3) Redundancy test - standalone mode"
echo "  4) Redundancy test - PRIMARY with failover"
echo "  5) Redundancy test - SECONDARY mode"
echo ""
read -p "Select option (1-5): " TEST_OPTION

MODE="standalone"
START_FROM_BLOCK="current"

case $TEST_OPTION in
    1)
        echo ""
        read -p "Start from which block? (enter number or 'current'): " START_FROM_BLOCK
        echo "   Will test historical sync from block $START_FROM_BLOCK"
        ;;
    2)
        echo "   Will monitor real-time events (standalone mode)"
        ;;
    3)
        MODE="primary"
        echo "   Will run as PRIMARY in standalone (no peer)"
        ;;
    4)
        MODE="primary"
        read -p "Listen on which UDP port? [9999]: " LISTEN_PORT
        LISTEN_PORT=${LISTEN_PORT:-9999}
        echo "   Will run as PRIMARY on port $LISTEN_PORT"
        echo "   ⚠️  Make sure to start SECONDARY on another machine/terminal!"
        ;;
    5)
        MODE="secondary"
        read -p "Listen on which UDP port? [9998]: " LISTEN_PORT
        LISTEN_PORT=${LISTEN_PORT:-9998}
        read -p "Primary address (host:port)? [127.0.0.1:9999]: " PEER_ADDR
        PEER_ADDR=${PEER_ADDR:-127.0.0.1:9999}
        echo "   Will run as SECONDARY on port $LISTEN_PORT"
        echo "   Will register with PRIMARY at $PEER_ADDR"
        ;;
esac

# Check if network config exists in Firestore
echo -e "\n[4/5] Checking Firestore configuration..."
echo "⚠️  Make sure Firestore has these documents:"
echo "   - contracts/$NETWORK (with wrapper addresses)"
echo "   - networks/$NETWORK (with RPC, fromBlock, lastSyncedBlock)"
echo ""
read -p "Press Enter when ready to continue, or Ctrl+C to abort..."

# Build command
CMD="python -u app.py $NETWORK homebase"

if [ "$MODE" != "standalone" ]; then
    CMD="$CMD --mode $MODE"

    if [ ! -z "$LISTEN_PORT" ]; then
        CMD="$CMD --listen-port $LISTEN_PORT"
    fi

    if [ "$MODE" == "secondary" ] && [ ! -z "$PEER_ADDR" ]; then
        CMD="$CMD --peer-address $PEER_ADDR"
    fi
fi

# Run indexer
echo -e "\n[5/5] Starting indexer..."
echo "Command: $CMD"
echo ""
echo "=================================================="
echo "  INDEXER RUNNING"
echo "  Press Ctrl+C to stop"
echo "=================================================="
echo ""

$CMD

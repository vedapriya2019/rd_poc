#!/bin/bash

# Check server status
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/desktop_server.pid"
LOG_FILE="$SCRIPT_DIR/logs/startup.log"

echo "🔍 DWService Desktop Server Status"
echo "===================================="

# Check PID file
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "✅ Status: RUNNING"
        echo "📊 PID: $PID"
    else
        echo "❌ Status: STOPPED (stale PID file)"
        rm -f "$PID_FILE"
    fi
else
    echo "❌ Status: STOPPED"
fi

# Check port
if lsof -i :8080 2>/dev/null | grep -q LISTEN; then
    echo "✅ Port 8080: LISTENING"
    IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
    echo "🌐 Access: http://$IP:8080"
else
    echo "❌ Port 8080: NOT LISTENING"
fi

# Show recent logs
if [ -f "$LOG_FILE" ]; then
    echo ""
    echo "📝 Recent Logs (last 5 lines):"
    echo "---"
    tail -5 "$LOG_FILE"
fi

echo ""

#!/bin/bash

# Stealth stop - no output
exec > /dev/null 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/desktop_server.pid"
DAEMON_PID_FILE="$SCRIPT_DIR/daemon.pid"

# Kill daemon
if [ -f "$DAEMON_PID_FILE" ]; then
    kill -9 $(cat "$DAEMON_PID_FILE") 2>/dev/null
    rm -f "$DAEMON_PID_FILE"
fi

# Kill server
if [ -f "$PID_FILE" ]; then
    kill -9 $(cat "$PID_FILE") 2>/dev/null
    rm -f "$PID_FILE"
fi

# Kill by name
pkill -9 -f "desktop_server.py" 2>/dev/null
pkill -9 -f "start_desktop.sh" 2>/dev/null

# Kill by port
lsof -ti :8080 2>/dev/null | xargs kill -9 2>/dev/null

sleep 1
exit 0

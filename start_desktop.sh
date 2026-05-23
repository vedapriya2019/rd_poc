#!/bin/bash

# DWService Agent - Stealth Mode Startup
# Runs completely silent in background with auto-restart

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$SCRIPT_DIR/desktop_server.pid"
DAEMON_PID_FILE="$SCRIPT_DIR/daemon.pid"
LOG_FILE="$LOG_DIR/startup.log"
SERVER_LOG="$LOG_DIR/desktop_server.log"
PORT=8080
MAX_RETRIES=3
RETRY_DELAY=5

# Redirect all output to /dev/null for stealth
exec > /dev/null 2>&1

# Create logs directory
mkdir -p "$LOG_DIR"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Get local IP
get_local_ip() {
    ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1
}

# Kill process using port
kill_port() {
    local port=$1
    local pids=$(lsof -ti :$port 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 2
    fi
}

# Kill old instances
kill_old_instances() {
    if [ -f "$PID_FILE" ]; then
        local old_pid=$(cat "$PID_FILE" 2>/dev/null)
        if [ -n "$old_pid" ]; then
            kill -9 "$old_pid" 2>/dev/null
        fi
        rm -f "$PID_FILE"
    fi
    
    pkill -9 -f "desktop_server.py" 2>/dev/null
    kill_port $PORT
    sleep 2
}

# Start server
start_server() {
    cd "$SCRIPT_DIR"
    
    python3 "$SCRIPT_DIR/core/desktop_server.py" >> "$SERVER_LOG" 2>&1 &
    local pid=$!
    
    echo "$pid" > "$PID_FILE"
    sleep 3
    
    if ps -p "$pid" > /dev/null 2>&1; then
        log "Server started (PID: $pid, Port: $PORT)"
        return 0
    else
        log "Server failed to start"
        return 1
    fi
}

# Monitor server
monitor_server() {
    local pid=$1
    local check_count=0
    
    while true; do
        sleep 10
        check_count=$((check_count + 1))
        
        if ! ps -p "$pid" > /dev/null 2>&1; then
            log "Server died (PID: $pid)"
            return 1
        fi
        
        if [ $((check_count % 3)) -eq 0 ]; then
            if ! lsof -i :$PORT 2>/dev/null | grep -q LISTEN; then
                log "Port $PORT not listening"
                return 1
            fi
        fi
        
        if [ $((check_count % 60)) -eq 0 ]; then
            log "Health: OK (PID: $pid)"
        fi
    done
}

# Main loop
main_loop() {
    local retry_count=0
    
    while true; do
        log "Starting (Attempt $((retry_count + 1)))"
        
        kill_old_instances
        
        if start_server; then
            local pid=$(cat "$PID_FILE")
            retry_count=0
            
            local ip=$(get_local_ip)
            log "Running: http://$ip:$PORT (PID: $pid)"
            
            monitor_server "$pid"
        fi
        
        retry_count=$((retry_count + 1))
        
        if [ $retry_count -ge $MAX_RETRIES ]; then
            log "Max retries, waiting 30s"
            sleep 30
            retry_count=0
        else
            sleep $RETRY_DELAY
        fi
    done
}

# Cleanup
cleanup() {
    log "Shutdown"
    
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        kill -9 "$pid" 2>/dev/null
        rm -f "$PID_FILE"
    fi
    
    pkill -9 -f "desktop_server.py" 2>/dev/null
    rm -f "$DAEMON_PID_FILE"
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# Daemonize
if [ "$1" != "--daemon" ]; then
    # Check if already running
    if [ -f "$DAEMON_PID_FILE" ]; then
        old_daemon_pid=$(cat "$DAEMON_PID_FILE")
        if ps -p "$old_daemon_pid" > /dev/null 2>&1; then
            exit 0
        fi
    fi
    
    # Start as daemon
    nohup "$0" --daemon >> "$LOG_FILE" 2>&1 &
    echo $! > "$DAEMON_PID_FILE"
    
    # Wait to verify startup
    sleep 3
    
    # Exit silently
    exit 0
fi

# Daemon mode
log "=========================================="
log "DWService Agent - Stealth Mode"
log "=========================================="

main_loop

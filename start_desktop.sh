#!/bin/bash
# Remote desktop server — stealth background mode with auto-heal.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$SCRIPT_DIR/desktop_server.pid"
WATCHDOG_PID_FILE="$SCRIPT_DIR/watchdog.pid"
LOG_FILE="$LOG_DIR/startup.log"
SERVER_LOG="$LOG_DIR/desktop_server.log"
PORT="${DESKTOP_PORT:-8080}"
HEALTH_INTERVAL=20
START_WAIT=4

export DESKTOP_PORT="$PORT"
export DESKTOP_LOG_DIR="$LOG_DIR"
export DESKTOP_STEALTH="${DESKTOP_STEALTH:-1}"

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTION]

  (no args)       Start server in background (stealth, auto-heal)
  --foreground    Run in this terminal (for debugging)
  --stop          Stop server, watchdog, and free port $PORT
  --status        Show status
  -h, --help      Help

Open:  http://127.0.0.1:$PORT/desktop
Health: http://127.0.0.1:$PORT/api/health
EOF
}

log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >>"$LOG_FILE"
}

get_local_ip() {
    ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" { print $2; exit }'
}

ensure_port_free() {
    local attempt pids
    for attempt in 1 2 3 4 5; do
        pids=$(lsof -ti :"$PORT" 2>/dev/null || true)
        [ -z "$pids" ] && return 0
        log "Freeing port $PORT (attempt $attempt, PIDs: $pids)"
        echo "$pids" | xargs kill -15 2>/dev/null || true
        sleep 0.4
        pids=$(lsof -ti :"$PORT" 2>/dev/null || true)
        [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 0.4
    done
    ! lsof -ti :"$PORT" >/dev/null 2>&1
}

stop_server_process() {
    if [ -f "$PID_FILE" ]; then
        local sp
        sp=$(cat "$PID_FILE" 2>/dev/null || true)
        [ -n "$sp" ] && kill -9 "$sp" 2>/dev/null || true
        rm -f "$PID_FILE"
    fi
    pkill -9 -f "core/desktop_server.py" 2>/dev/null || true
}

stop_watchdog() {
    if [ -f "$WATCHDOG_PID_FILE" ]; then
        local wp
        wp=$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)
        [ -n "$wp" ] && kill -9 "$wp" 2>/dev/null || true
        rm -f "$WATCHDOG_PID_FILE"
    fi
    pkill -9 -f "start_desktop.sh --watchdog" 2>/dev/null || true
}

is_listening() {
    lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

http_health_ok() {
    python3 - "$PORT" <<'PY' 2>/dev/null
import sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen('http://127.0.0.1:{}/api/health'.format(port), timeout=4) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

start_server() {
    stop_server_process
    ensure_port_free || return 1
    cd "$SCRIPT_DIR" || return 1
    mkdir -p "$LOG_DIR"
    DESKTOP_PORT="$PORT" DESKTOP_STEALTH=1 DESKTOP_LOG_DIR="$LOG_DIR" \
        nohup python3 "$SCRIPT_DIR/core/desktop_server.py" >>"$SERVER_LOG" 2>&1 &
    local pid=$!
    echo "$pid" >"$PID_FILE"
    local i=0
    while [ "$i" -lt "$START_WAIT" ]; do
        sleep 1
        if ps -p "$pid" >/dev/null 2>&1 && is_listening; then
            log "Server running PID=$pid http://127.0.0.1:$PORT"
            return 0
        fi
        i=$((i + 1))
    done
    log "Server failed to start. Last log lines:"
    tail -5 "$SERVER_LOG" >>"$LOG_FILE" 2>/dev/null || true
    return 1
}

watchdog_loop() {
    log "Watchdog started (port $PORT)"
    while true; do
        sleep "$HEALTH_INTERVAL"
        if is_listening && http_health_ok; then
            continue
        fi
        log "Watchdog: server down or unhealthy — restarting"
        start_server || log "Watchdog: restart failed"
    done
}

cmd_stop() {
    stop_watchdog
    stop_server_process
    ensure_port_free || true
    echo "Stopped. Port $PORT is free."
}

cmd_status() {
    local ip
    ip=$(get_local_ip || echo "127.0.0.1")
    echo "Remote desktop (port $PORT)"
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if ps -p "$pid" >/dev/null 2>&1; then
            echo "Server:  RUNNING (PID $pid)"
        else
            echo "Server:  STOPPED (stale PID file)"
        fi
    else
        echo "Server:  STOPPED"
    fi
    if [ -f "$WATCHDOG_PID_FILE" ]; then
        local wp
        wp=$(cat "$WATCHDOG_PID_FILE")
        if ps -p "$wp" >/dev/null 2>&1; then
            echo "Watchdog: RUNNING (PID $wp)"
        else
            echo "Watchdog: STOPPED"
        fi
    else
        echo "Watchdog: OFF"
    fi
    if is_listening; then
        echo "URL:     http://127.0.0.1:$PORT/desktop"
        echo "Network: http://$ip:$PORT/desktop"
        if http_health_ok; then
            echo "Health:  OK"
        else
            echo "Health:  FAIL (watchdog will restart if enabled)"
        fi
    else
        echo "Port $PORT is not listening — run: ./start_desktop"
    fi
}

cmd_foreground() {
    if ! command -v python3 &>/dev/null; then
        echo "Python 3 is required." >&2
        exit 1
    fi
    stop_watchdog
    stop_server_process
    ensure_port_free || { echo "Cannot free port $PORT" >&2; exit 1; }
    export DESKTOP_STEALTH=0
    cd "$SCRIPT_DIR"
    local ip
    ip=$(get_local_ip || echo "127.0.0.1")
    echo "http://127.0.0.1:$PORT/desktop"
    echo "http://$ip:$PORT/desktop"
    echo "Press Ctrl+C to stop."
    exec python3 "$SCRIPT_DIR/core/desktop_server.py"
}

cmd_start() {
    if ! command -v python3 &>/dev/null; then
        echo "Python 3 is required." >&2
        exit 1
    fi

    # Already up?
    if is_listening && http_health_ok; then
        cmd_status
        echo ""
        echo "Already running."
        return 0
    fi

    stop_watchdog
    if ! start_server; then
        echo "Failed to start. Check $SERVER_LOG" >&2
        exit 1
    fi

    # Watchdog (separate process; never kills itself)
    nohup /bin/bash "$0" --watchdog >>"$LOG_FILE" 2>&1 &
    echo $! >"$WATCHDOG_PID_FILE"
    sleep 1
    if ! ps -p "$(cat "$WATCHDOG_PID_FILE")" >/dev/null 2>&1; then
        log "Watchdog failed to start (server still running)"
        rm -f "$WATCHDOG_PID_FILE"
    fi

    cmd_status
    echo ""
    echo "Started. Open http://127.0.0.1:$PORT/desktop"
}

# --- entry ---
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --stop) cmd_stop; exit 0 ;;
    --status) cmd_status; exit 0 ;;
    --foreground|-f) cmd_foreground ;;
    --watchdog) watchdog_loop ;;
    --daemon|--daemon-worker) cmd_start ;;
    "") cmd_start ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
esac

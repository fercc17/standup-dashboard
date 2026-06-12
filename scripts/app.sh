#!/usr/bin/env bash
#
# Start / stop the IS SRE Standup Dashboard in the background.
#
#   scripts/app.sh start     # launch (writes PID + log under .run/)
#   scripts/app.sh stop      # halt
#   scripts/app.sh restart
#   scripts/app.sh status
#
# The server binds 127.0.0.1:8765 (see src/standup_dashboard/config.py).
# For a foreground run (e.g. PyCharm's Run button) use main.py instead.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Bind host/port (override with STANDUP_HOST / STANDUP_PORT; default loopback).
# Use STANDUP_HOST=0.0.0.0 to reach the dashboard from other devices on the LAN.
HOST="${STANDUP_HOST:-127.0.0.1}"
PORT="${STANDUP_PORT:-8765}"
export STANDUP_HOST="$HOST" STANDUP_PORT="$PORT"

PROBE_HOST="127.0.0.1"   # loopback always works, even when bound to 0.0.0.0
if [[ "$HOST" == "0.0.0.0" ]]; then
  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  URL="http://${LAN_IP:-$HOST}:${PORT}"
else
  URL="http://${HOST}:${PORT}"
fi
RUN_DIR="$ROOT/.run"
PID_FILE="$RUN_DIR/app.pid"
LOG_FILE="$RUN_DIR/app.log"

mkdir -p "$RUN_DIR"

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

wait_for_port() {
  for _ in $(seq 1 50); do
    if (exec 3<>"/dev/tcp/${PROBE_HOST}/${PORT}") 2>/dev/null; then
      exec 3>&- 3<&- 2>/dev/null || true
      return 0
    fi
    sleep 0.2
  done
  return 1
}

start() {
  if is_running; then
    echo "Already running (PID $(cat "$PID_FILE")) → ${URL}"
    return 0
  fi
  echo "Starting dashboard..."
  nohup uv run python -m standup_dashboard >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  if wait_for_port; then
    echo "Started (PID $(cat "$PID_FILE")) → ${URL}"
    echo "Logs: ${LOG_FILE}"
  else
    echo "Server did not open ${HOST}:${PORT} in time; check ${LOG_FILE}" >&2
    return 1
  fi
}

stop() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 25); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null || true
    echo "Stopped (PID ${pid})"
  else
    echo "Not running"
  fi
  rm -f "$PID_FILE"
}

status() {
  if is_running; then
    echo "running (PID $(cat "$PID_FILE")) → ${URL}"
  else
    echo "stopped"
  fi
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  *) echo "usage: $0 {start|stop|restart|status}" >&2; exit 1 ;;
esac

#!/bin/bash
# Endless catch-all wave loop. Waves of N accounts, pace P. Ctrl-C to stop.
# Env: TREG_PYTHON (default=python), N (default=50), PACE (default=25), TREG_DATA (log dir, default=here)
cd "$(dirname "$0")"
export PYTHONPATH="" PYTHONIOENCODING=utf-8
export HOME="${HOME:-$(pwd)}" USERPROFILE="${USERPROFILE:-$HOME}" PROCESSOR_ARCHITECTURE="${PROCESSOR_ARCHITECTURE:-AMD64}"
PY="${TREG_PYTHON:-python}"
N="${N:-50}"; PACE="${PACE:-25}"
LOG="${TREG_DATA:-.}/ca_wave.log"
WAVE=0
while true; do
  WAVE=$((WAVE+1))
  echo "[$(date +%H:%M:%S)] === CATCHALL WAVE $WAVE (n=$N pace=$PACE) ===" >> "$LOG"
  "$PY" treg_catchall.py "$N" --pace "$PACE" >> "$LOG" 2>&1
  sleep 60
done

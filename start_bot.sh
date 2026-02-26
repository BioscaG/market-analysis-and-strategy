#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || true
nohup python3 -u bot.py > log.out 2>&1 &
echo "Bot started (PID: $!)"

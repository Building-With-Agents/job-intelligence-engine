#!/bin/bash
# Demo stack startup script
# Run this before the demo — starts JIE on :8020

set -euo pipefail
cd "$(dirname "$0")/../.."

echo "=== Starting JIE on port 8020 ==="
echo "Open Terminal 2 and run: cd /Users/kuike/Desktop/wfd-os && honcho start"
echo "Open Terminal 3 and run: python scripts/smoke/laborpulse/real_query.py"
echo ""
echo "Starting JIE..."
source .venv/bin/activate
exec uvicorn analytics.api.app:app --host 127.0.0.1 --port 8020

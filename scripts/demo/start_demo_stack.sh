#!/bin/bash
# Demo stack startup script
# Run this before the demo — starts JIE on the analytics API default port (8000)

set -euo pipefail
cd "$(dirname "$0")/../.."

PORT="${ANALYTICS_API_PORT:-8000}"

echo "=== Starting JIE on port ${PORT} ==="
echo "Alternative: python scripts/run_analytics_api.py"
echo "Open Terminal 2 and run: cd /path/to/wfd-os && honcho start"
echo "Open Terminal 3 and run: python scripts/smoke/laborpulse/real_query.py"
echo ""
echo "Starting JIE..."
source .venv/bin/activate
exec uvicorn analytics.api.app:app --host 127.0.0.1 --port "${PORT}"

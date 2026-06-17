#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:$PATH"

# Kill anything already on these ports
lsof -ti :8000 | xargs kill -9 2>/dev/null
lsof -ti :5173 | xargs kill -9 2>/dev/null
sleep 0.5

echo "Starting backend..."
cd "$ROOT/backend"
"$ROOT/.venv/bin/python" -m uvicorn main:app --port 8000 --log-level warning &
BACKEND_PID=$!

echo "Starting frontend..."
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

trap "echo 'Stopping...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT INT TERM

# Open browser once Vite is ready
sleep 3 && open http://localhost:5173 &

echo ""
echo "🏀  NBA Stat Predictor → http://localhost:5173"
echo "    Press Ctrl+C to stop."
echo ""

wait $FRONTEND_PID

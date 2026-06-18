#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:$PATH"

# Kill anything already on these ports
lsof -ti :8000 | xargs kill -9 2>/dev/null
lsof -ti :5173 | xargs kill -9 2>/dev/null
sleep 0.5

echo "Starting backend..."
cd "$ROOT/backend"
"$ROOT/.venv/bin/python" -m uvicorn main:app --port 8000 &
BACKEND_PID=$!

echo "Starting frontend..."
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

trap "echo 'Stopping...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; wait" EXIT INT TERM

# Wait for backend to be ready before opening browser
echo "Waiting for backend..."
for i in $(seq 1 20); do
  if curl -s http://localhost:8000/games/today > /dev/null 2>&1; then
    echo ""
    echo "🏀  NBA Stat Predictor → http://localhost:5173"
    echo "    Press Ctrl+C to stop."
    echo ""
    open http://localhost:5173
    break
  fi
  sleep 1
done

wait $FRONTEND_PID

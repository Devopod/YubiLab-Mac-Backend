#!/bin/bash
# YubiLab Worker Start Script

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

echo "========================================="
echo "  YubiLab AI Agent Worker v2.0"
echo "  Fully Autonomous - Like Devin/z.ai"
echo "========================================="
echo ""
echo "Host: $HOST"
echo "Port: $PORT"
echo "Groq Model: $GROQ_DEFAULT_MODEL"
echo ""

# Start with gunicorn + eventlet for WebSocket support
gunicorn app:app \
    --bind $HOST:$PORT \
    --worker-class eventlet \
    --workers 1 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -

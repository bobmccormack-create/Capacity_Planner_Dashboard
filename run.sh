#!/bin/bash
# ============================================================
#  Capacity Planner - one-click setup and launch (Mac/Linux)
#  Run with: ./run.sh
#  Safe to run again later - just launches faster on repeat runs.
# ============================================================
set -e
cd "$(dirname "$0")"

echo ""
echo "=== Capacity Planner setup ==="
echo "Working folder: $(pwd)"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 was not found on this computer."
    echo "Install it from https://www.python.org/downloads/"
    exit 1
fi

if [ ! -f ".venv/bin/python" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists, skipping creation."
fi

echo ""
echo "Installing dependencies (this may take a minute the first time)..."
.venv/bin/python -m pip install --upgrade pip > /dev/null
.venv/bin/python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
    echo ""
    echo "No .env file found - creating one from .env.example."
    echo "You can edit .env later to add your Zoho credentials."
    cp .env.example .env
fi

echo ""
echo "=== Starting Capacity Planner ==="
echo "A browser tab should open automatically."
echo "Press Ctrl+C to stop the app."
echo ""
.venv/bin/python -m streamlit run main.py

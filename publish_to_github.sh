#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/felixrieth/ECO302-Mikro-A-Dashboard.git"

git init
git branch -M main
git add .
git commit -m "Initial Streamlit ECO302 dashboard" || true
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"
git push -u origin main --force

echo "Pushed to $REPO_URL"

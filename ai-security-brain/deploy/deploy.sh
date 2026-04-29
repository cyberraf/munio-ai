#!/bin/bash
set -euo pipefail

# ── AI Security Brain — Remote Deploy Script ─────────────────────────────────
# Run from your local machine. Updates and restarts the production stack.
# Usage: ./deploy.sh [EC2_IP]

EC2_IP="${1:-YOUR_EC2_IP}"
EC2_USER="${2:-ubuntu}"
REPO_DIR="ai-security-brain"

echo "=== Deploying AI Security Brain to $EC2_USER@$EC2_IP ==="

ssh "$EC2_USER@$EC2_IP" << REMOTE
  set -euo pipefail
  cd ~/$REPO_DIR

  echo "Pulling latest code..."
  git pull --ff-only

  echo "Building and restarting services..."
  cd deploy
  docker compose -f docker-compose.prod.yml up --build -d

  echo "Cleaning up old images..."
  docker image prune -f

  echo "=== Deploy complete ==="
  docker compose -f docker-compose.prod.yml ps
REMOTE

echo "Done. Dashboard: https://\$(grep DOMAIN $REPO_DIR/deploy/.env | cut -d= -f2)"

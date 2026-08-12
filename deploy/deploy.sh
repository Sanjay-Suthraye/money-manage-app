#!/bin/sh
# Deploy: push local main to GitHub, then the server pulls it and restarts.
# The server is a git checkout of origin/main using a read-only deploy key;
# its .env, data/ and .venv are untracked and never touched by pulls.
set -e

IP="${MONEY_SERVER_IP:-13.234.241.7}"
KEY="$HOME/.ssh/lightsail-ap-south-1.pem"

git -C "$(dirname "$0")/.." push origin main

ssh -i "$KEY" ubuntu@"$IP" "
  set -e
  cd ~/money_manager
  git pull --ff-only
  .venv/bin/pip install -q -r requirements.txt
  sudo systemctl restart money-manager
"
echo "Deployed to https://$IP"

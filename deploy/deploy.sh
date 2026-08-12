#!/bin/sh
# Push the current working tree to the Lightsail server and restart the app.
# Server-side .env, data/ and .venv are excluded and never touched
# (plain --delete does not remove excluded files on the receiver).
set -e

IP="${MONEY_SERVER_IP:?set MONEY_SERVER_IP=<static-ip> (or edit this script)}"
KEY="$HOME/.ssh/lightsail-ap-south-1.pem"

rsync -avz --delete -e "ssh -i $KEY" \
  --exclude .git --exclude data --exclude .venv \
  --exclude .env --exclude api_keys.env \
  --exclude __pycache__ --exclude '*.pyc' --exclude '*.xlsx' \
  "$(dirname "$0")/../" ubuntu@"$IP":/home/ubuntu/money_manager/

ssh -i "$KEY" ubuntu@"$IP" "sudo systemctl restart money-manager"
echo "Deployed to https://$IP"

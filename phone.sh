#!/bin/sh
# Print the URLs (and a scannable QR code) for opening the app on your iPhone.
PORT="${PORT:-8010}"
exec "$HOME/miniconda3/envs/money/bin/python" - "$PORT" <<'EOF'
import json
import socket
import subprocess
import sys

port = sys.argv[1]


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


mdns = sh(["scutil", "--get", "LocalHostName"])
lan_ip = sh(["ipconfig", "getifaddr", "en0"]) or sh(["ipconfig", "getifaddr", "en1"])

tailscale_url = None
ts_bin = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
raw = sh([ts_bin, "status", "--json"]) or sh(["tailscale", "status", "--json"])
if raw:
    try:
        status = json.loads(raw)
        dns_name = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
        online = (status.get("Self") or {}).get("Online")
        if dns_name and online:
            tailscale_url = f"http://{dns_name}:{port}"
    except json.JSONDecodeError:
        pass

print("Open the Money app on your iPhone:\n")
if mdns:
    print(f"  Same Wi-Fi:   http://{mdns}.local:{port}")
if lan_ip:
    print(f"  (IP fallback) http://{lan_ip}:{port}")
if tailscale_url:
    print(f"  Anywhere:     {tailscale_url}   (needs Tailscale ON on the phone)")
else:
    print("  Anywhere:     Tailscale not connected — open the Tailscale app and sign in")

best = tailscale_url or (f"http://{mdns}.local:{port}" if mdns else f"http://{lan_ip}:{port}")
print(f"\nScan with the iPhone camera → opens {best}")
try:
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(best)
    qr.print_ascii(invert=True)
except ImportError:
    print("(pip install qrcode for a scannable code)")
print("Then tap Share → Add to Home Screen for the app feel.")
print("Note: the Mac must be awake — keep it plugged in for phone use.")
EOF

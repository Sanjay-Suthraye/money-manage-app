#!/bin/sh
# Print the URLs (and a scannable QR code) for opening the app on your iPhone.
PORT="${PORT:-8010}"
exec "$HOME/miniconda3/envs/money/bin/python" - "$PORT" <<'EOF'
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

print("Open the Money app on your iPhone:\n")
if mdns:
    print(f"  Same Wi-Fi:   http://{mdns}.local:{port}")
if lan_ip:
    print(f"  (IP fallback) http://{lan_ip}:{port}")

best = f"http://{mdns}.local:{port}" if mdns else f"http://{lan_ip}:{port}"
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

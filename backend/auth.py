"""Single-user authentication: stdlib scrypt password hashing plus a small CLI
for setting/rotating the password.

Hash format: scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>

Run:  python -m backend.auth set-password [--generate]
(restart the service afterwards — settings are read at import time)
"""

import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path

from backend.settings import PROJECT_ROOT

SCRYPT_N = 2**14  # 16 MiB — under hashlib's default 32 MiB maxmem cap
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p),
                                dklen=len(bytes.fromhex(hash_hex)))
        return hmac.compare_digest(digest, bytes.fromhex(hash_hex))
    except (ValueError, AttributeError):
        return False


def _update_env_file(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)


def set_password(generate: bool = False) -> None:
    import getpass

    from dotenv import dotenv_values

    env_path = PROJECT_ROOT / ".env"
    current = dotenv_values(env_path) if env_path.exists() else {}

    username = input(f"Username [{current.get('MONEY_USERNAME') or 'sanjay'}]: ").strip() \
        or current.get("MONEY_USERNAME") or "sanjay"

    if generate:
        password = secrets.token_urlsafe(12)
        print(f"\nGenerated password (save it now, it is not stored in plain text):\n\n    {password}\n")
    else:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            sys.exit(1)
        if len(password) < 8:
            print("Use at least 8 characters.")
            sys.exit(1)

    updates = {
        "MONEY_USERNAME": username,
        "MONEY_PASSWORD_HASH": hash_password(password),
    }
    if not current.get("MONEY_SESSION_SECRET"):
        updates["MONEY_SESSION_SECRET"] = secrets.token_hex(32)
    _update_env_file(env_path, updates)
    print(f"Wrote credentials for '{username}' to {env_path}.")
    print("Restart the server to apply (settings are read at startup).")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "set-password":
        set_password(generate="--generate" in sys.argv)
    else:
        print(__doc__)
        sys.exit(1)

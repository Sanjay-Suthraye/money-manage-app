"""Password hashing (stdlib scrypt) and stateless HMAC session tokens.

Hash format: scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>
User management lives in `python -m backend.users`.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
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


# Burned against unknown usernames so login timing can't enumerate users.
DUMMY_HASH = hash_password(secrets.token_hex(16))


# ---- Session tokens: base64url(json payload) + "." + base64url(hmac-sha256) ----
# Stateless, so restarts don't log the user out and no table is needed.
# Revoking every session = rotate MONEY_SESSION_SECRET and restart.

SESSION_TTL_SECONDS = 30 * 24 * 3600  # ~30 days


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(secret: str, payload: bytes) -> str:
    return _b64(hmac.new(secret.encode(), payload, hashlib.sha256).digest())


def mint_session(secret: str, username: str) -> str:
    body = _b64(json.dumps({"u": username, "exp": int(time.time()) + SESSION_TTL_SECONDS}).encode())
    return f"{body}.{_sign(secret, body.encode())}"


def verify_session(secret: str, token: str) -> str | None:
    """Return the username if the token is valid and unexpired, else None."""
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(secret, body.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data["u"] if int(data["exp"]) > time.time() else None
    except Exception:
        return None


if __name__ == "__main__":
    print("Passwords now live in the users table — use:\n"
          "    python -m backend.users set-password <username> [--generate]\n"
          "    python -m backend.users add <username> [--generate]")
    sys.exit(1)

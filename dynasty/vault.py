"""PIN lock for the published app data.

The site is public on GitHub Pages, so a PIN screen alone wouldn't protect anything: the data
files would still be downloadable. Instead, every file except the public ones (League and Past
trades screens) is gzipped and encrypted into one `vault.json`:

    key = PBKDF2-HMAC-SHA256(PIN, salt, ITERATIONS) -> 32 bytes
    ct  = AES-256-GCM(key, iv, gzip(json {file: data, ...}))

The browser derives the same key with WebCrypto (site/app.js). The salt is fixed for this app so a
device that has unlocked once can keep its derived key across daily refreshes.

A 4-digit PIN has 10,000 combinations; ITERATIONS makes each guess cost about half a second of a
CPU core, so trying them all offline takes on the order of an hour or two. It keeps casual
visitors (league-mates with the link) out; it is not strong protection against a determined one.
The PIN comes from the DYNASTY_PIN environment variable (a GitHub Actions secret). Without it the
data is published unencrypted, as before.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

PUBLIC = ("meta.json", "teams.json", "trades.json", "history.json", "vault.json")
# Open files keep only what the open screens (League, Past trades, History) display; the full
# versions go into the vault and replace them in the app once unlocked.
OPEN_FIELDS = {
    "teams": ("roster_id", "team", "owner", "avatar", "wins", "losses", "ties", "pf", "faab_left",
              "p_playoffs", "value", "avg_age", "window", "strength"),
}
META_HIDDEN = ("my_roster_id", "value_dates")
SALT = hashlib.sha256(b"ff-dynasty-vault-v1").digest()[:16]
ITERATIONS = 600_000
VERSION = 1


def derive_key(pin: str, iterations: int = ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), SALT, iterations, dklen=32)


def seal(files: dict, pin: str, iterations: int = ITERATIONS) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = derive_key(pin, iterations)
    iv = os.urandom(12)
    plain = gzip.compress(json.dumps(files, separators=(",", ":")).encode(), mtime=0)
    ct = AESGCM(key).encrypt(iv, plain, None)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return {"v": VERSION, "kdf": "PBKDF2-SHA256", "iter": iterations, "salt": b64(SALT), "iv": b64(iv),
            "alg": "AES-256-GCM", "zip": "gzip", "files": sorted(files), "ct": b64(ct)}


def open_vault(vault: dict, pin: str) -> dict:
    """Decrypt (tests and local checks). Raises on a wrong PIN."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = derive_key(pin, vault["iter"])
    plain = AESGCM(key).decrypt(base64.b64decode(vault["iv"]), base64.b64decode(vault["ct"]), None)
    return json.loads(gzip.decompress(plain))


def lock(data_dir: Path, pin: str | None) -> dict:
    """Move every non-public JSON file in data_dir into vault.json. No PIN: leave everything open."""
    data_dir = Path(data_dir)
    vault_file = data_dir / "vault.json"
    if not pin:
        if vault_file.exists():
            vault_file.unlink()
        log.warning("DYNASTY_PIN not set: app data is published unlocked")
        return {"locked": 0}
    pin = str(pin).strip()
    private = sorted(p for p in data_dir.glob("*.json") if p.name not in PUBLIC)
    files = {p.stem: json.loads(p.read_text()) for p in private}
    # trimmed open copies of teams and meta; the full ones ride in the vault
    trimmed = {}
    teams_f, meta_f = data_dir / "teams.json", data_dir / "meta.json"
    if teams_f.exists():
        full = json.loads(teams_f.read_text())
        files["teams"] = full
        keep = OPEN_FIELDS["teams"]
        trimmed[teams_f] = [{k: t.get(k) for k in keep if k in t} for t in full]
    if meta_f.exists():
        full = json.loads(meta_f.read_text())
        files["meta"] = full
        trimmed[meta_f] = {k: v for k, v in full.items() if k not in META_HIDDEN}
    vault_file.write_text(json.dumps(seal(files, pin), separators=(",", ":")))
    for f, data in trimmed.items():
        f.write_text(json.dumps(data, separators=(",", ":")))
    for p in private:
        p.unlink()
    return {"locked": len(private)}


# ---------------------------------------------------------------------------- whole files
FILE_MAGIC = b"DYNV1"


def encrypt_file(src: Path, dst: Path, pin: str) -> None:
    """Encrypt a file (the database backup) with the PIN: magic + iv + AES-256-GCM ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(derive_key(pin)).encrypt(iv, Path(src).read_bytes(), None)
    Path(dst).write_bytes(FILE_MAGIC + iv + ct)


def decrypt_file(src: Path, dst: Path, pin: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = Path(src).read_bytes()
    if not raw.startswith(FILE_MAGIC):
        raise ValueError("not an encrypted backup")
    n = len(FILE_MAGIC)
    Path(dst).write_bytes(AESGCM(derive_key(pin)).decrypt(raw[n:n + 12], raw[n + 12:], None))


if __name__ == "__main__":
    # python -m dynasty.vault encrypt-file|decrypt-file SRC DST   (PIN from DYNASTY_PIN)
    import sys
    cmd, src, dst = sys.argv[1:4]
    pin = os.environ.get("DYNASTY_PIN")
    if not pin:
        sys.exit("DYNASTY_PIN is not set")
    (encrypt_file if cmd == "encrypt-file" else decrypt_file)(Path(src), Path(dst), pin)

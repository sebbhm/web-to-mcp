"""Reading Google Chrome cookies on macOS.

Chrome keeps its cookies in one SQLite database per profile and encrypts their values with AES-128-CBC,
using a key derived (PBKDF2-SHA1) from the "Chrome Safe Storage" secret in the Keychain. Reading that
secret triggers a macOS prompt: "Allow" covers this call, "Always Allow" covers any program going
through /usr/bin/security. The derived key stays in memory, never on disk.

Two cookie sources are supported, both written by the same Chrome binary (so the same Keychain secret
decrypts them):

- "ai" (default): Chrome AI, a dedicated Chrome instance for the AI, launched with
  --user-data-dir=$CHROME_AI_PROFILE (default ~/.chrome-ai-profile);
- "chrome": the user's regular Chrome profiles.

The source is picked by the WEBAPP_CHROME environment variable unless a caller passes it explicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CHROME_DIR = Path.home() / "Library/Application Support/Google/Chrome"
CHROME_AI_DEFAULT_DIR = "~/.chrome-ai-profile"  # kept unexpanded: it is also what the status tool shows
CHROME_AI_DEFAULT_PORT = 9333
CHROME_SOURCES = ("ai", "chrome")
KEYCHAIN_SERVICE = "Chrome Safe Storage"
_EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)


class KeychainError(RuntimeError):
    """The "Chrome Safe Storage" secret is unavailable (prompt denied or cancelled)."""


def env(name: str | None) -> str | None:
    """Non-empty environment variable; ignores unfilled MCPB templates ("${user_config.x}")."""
    value = (os.environ.get(name) or "").strip() if name else ""
    return value if value and not value.startswith("${") else None


def resolve_chrome_source(source: str | None = None) -> str:
    """Normalised cookie source: "ai" or "chrome". None (or empty) reads WEBAPP_CHROME (default "ai")."""
    value = (source or env("WEBAPP_CHROME") or "ai").strip().lower()
    if value not in CHROME_SOURCES:
        raise ValueError(
            f'Unknown Chrome source {value!r} (WEBAPP_CHROME or chrome=): use "ai" for Chrome AI '
            'or "chrome" for the regular Chrome profiles.'
        )
    return value


def resolve_user_data_dir(source: str | None = None) -> Path:
    """Chrome user-data-dir to read cookies from: Chrome AI's ("ai", $CHROME_AI_PROFILE or
    ~/.chrome-ai-profile) or the regular Chrome one ("chrome"). None reads WEBAPP_CHROME (default "ai")."""
    if resolve_chrome_source(source) == "chrome":
        return CHROME_DIR
    return Path(env("CHROME_AI_PROFILE") or CHROME_AI_DEFAULT_DIR).expanduser()


def chrome_ai_port() -> int:
    """Remote debugging port of Chrome AI ($CHROME_AI_PORT, default 9333)."""
    try:
        return int(env("CHROME_AI_PORT") or CHROME_AI_DEFAULT_PORT)
    except ValueError:
        return CHROME_AI_DEFAULT_PORT


@dataclass(frozen=True)
class Cookie:
    name: str
    value: str
    host: str
    path: str
    expires: datetime | None  # None: session cookie
    last_access: datetime | None
    profile: str  # profile folder ("Default", "Profile 1"…)
    profile_name: str  # name displayed in Chrome

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires <= datetime.now(timezone.utc)


def _chrome_time(microseconds: int) -> datetime | None:
    """Chrome dates count microseconds since January 1st, 1601; 0 = no date."""
    return _EPOCH_1601 + timedelta(microseconds=microseconds) if microseconds else None


class ChromeCookies:
    def __init__(self, user_data_dir: Path | str | None = None, keychain_service: str = KEYCHAIN_SERVICE):
        # Resolved here rather than at import time, so WEBAPP_CHROME / CHROME_AI_PROFILE set after import count.
        self.user_data_dir = Path(user_data_dir).expanduser() if user_data_dir else resolve_user_data_dir()
        self.keychain_service = keychain_service
        self._key: bytes | None = None

    def profiles(self) -> dict[str, str]:
        """Profiles that have a cookie database: folder → name displayed in Chrome. Empty when the
        user-data-dir does not exist yet (Chrome AI never started): the login flow then takes over."""
        if not self.user_data_dir.is_dir():
            return {}
        names: dict[str, str] = {}
        try:
            info = json.loads((self.user_data_dir / "Local State").read_text())["profile"]["info_cache"]
            names = {folder: entry.get("name") or folder for folder, entry in info.items()}
        except (OSError, KeyError, ValueError):
            pass
        return {db.parent.name: names.get(db.parent.name, db.parent.name) for db in sorted(self.user_data_dir.glob("*/Cookies"))}

    def read(self, domain: str, names: set[str] | None = None, profile: str | None = None) -> list[Cookie]:
        """Cookies of the domain and its subdomains, across all profiles (or one: folder or display name)."""
        cookies: list[Cookie] = []
        for folder, display in self.profiles().items():
            if profile and profile not in (folder, display):
                continue
            cookies += self._read_profile(folder, display, domain, names)
        return cookies

    def _read_profile(self, folder: str, display: str, domain: str, names: set[str] | None) -> list[Cookie]:
        source = self.user_data_dir / folder / "Cookies"
        with tempfile.TemporaryDirectory() as tmp:
            # Chrome keeps the database open: read a copy. Any journal goes along with the copy,
            # so that SQLite cleanly rolls back a write in progress.
            db = Path(tmp) / "Cookies"
            shutil.copy2(source, db)
            journal = source.with_name("Cookies-journal")
            if journal.exists():
                shutil.copy2(journal, db.with_name("Cookies-journal"))
            con = sqlite3.connect(db)
            try:
                version = int(con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()[0])
                # CAST: some databases store encrypted_value as TEXT; without it, SQLite tries to
                # UTF-8 decode the encrypted bytes and raises OperationalError.
                rows = con.execute(
                    "SELECT host_key, name, value, CAST(encrypted_value AS BLOB), path, expires_utc, last_access_utc FROM cookies "
                    "WHERE host_key = ? OR host_key = ? OR host_key LIKE ?",
                    (domain, "." + domain, "%." + domain),
                ).fetchall()
            finally:
                con.close()
        cookies = []
        for host, name, value, encrypted, path, expires, last_access in rows:
            if names and name not in names:
                continue
            if not value and encrypted:
                value = self._decrypt(encrypted, host, version)
            cookies.append(Cookie(name, value, host, path, _chrome_time(expires), _chrome_time(last_access), folder, display))
        return cookies

    def _decrypt(self, encrypted: bytes, host: str, db_version: int) -> str:
        if encrypted[:3] != b"v10":
            raise ValueError(f"Unknown cookie encryption ({encrypted[:3]!r}): the engine needs updating for this Chrome format.")
        decryptor = Cipher(algorithms.AES(self._aes_key()), modes.CBC(b" " * 16)).decryptor()
        unpadder = padding.PKCS7(128).unpadder()
        data = unpadder.update(decryptor.update(encrypted[3:]) + decryptor.finalize()) + unpadder.finalize()
        # Since database version 24, the value is prefixed with the SHA-256 of the domain.
        if db_version >= 24 and data[:32] == hashlib.sha256(host.encode()).digest():
            data = data[32:]
        return data.decode("utf-8")

    def _aes_key(self) -> bytes:
        if self._key is None:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-w", "-s", self.keychain_service],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise KeychainError(
                    f'Keychain access denied or cancelled for "{self.keychain_service}" ({result.stderr.strip()}). '
                    'Retry the request and click "Allow" in the macOS prompt.'
                )
            self._key = hashlib.pbkdf2_hmac("sha1", result.stdout.strip().encode(), b"saltysalt", 1003, 16)
        return self._key

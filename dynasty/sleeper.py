"""Thin, polite client for the public Sleeper API (https://docs.sleeper.com).

- No auth needed; everything is read-only.
- Sleeper asks callers to stay under ~1,000 calls per minute. We space calls out and
  retry on 429/5xx with backoff.
- Responses can be cached on disk with a time-to-live so repeat runs are cheap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app/v1"

# Cache lifetimes in seconds.
NO_CACHE = 0
HOUR = 3600
DAY = 86400
FOREVER = None  # completed seasons never change


class SleeperError(RuntimeError):
    pass


class SleeperClient:
    def __init__(
        self,
        cache_dir: Path | None = None,
        session: requests.Session | None = None,
        min_interval: float = 0.08,
        retries: int = 4,
        timeout: float = 30,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "dynasty-analyzer/0.1")
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._last_call = 0.0
        self.calls = 0

    # ---- core -------------------------------------------------------------
    def get(self, path: str, ttl: float | None = HOUR) -> Any:
        """GET a path like 'league/123/rosters'. ttl=None caches forever, 0 disables cache."""
        path = path.strip("/")
        cached = self._read_cache(path, ttl)
        if cached is not None:
            return cached["body"]

        url = f"{BASE_URL}/{path}"
        delay = 1.0
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise SleeperError(f"{url}: {exc}") from exc
                time.sleep(delay)
                delay *= 2
                continue
            self.calls += 1
            if resp.status_code == 200:
                body = resp.json() if resp.content else None
                self._write_cache(path, body, ttl)
                return body
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                log.warning("Sleeper %s on %s, retrying in %.0fs", resp.status_code, path, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise SleeperError(f"{url}: HTTP {resp.status_code}")
        raise SleeperError(f"{url}: out of retries")

    # ---- cache ------------------------------------------------------------
    def _cache_file(self, path: str) -> Path | None:
        if not self.cache_dir:
            return None
        name = hashlib.sha1(path.encode()).hexdigest()[:16]
        return self.cache_dir / f"{name}.json"

    def _read_cache(self, path: str, ttl: float | None) -> dict | None:
        f = self._cache_file(path)
        if f is None or ttl == 0 or not f.exists():
            return None
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("path") != path:
            return None
        if ttl is not None and time.time() - data.get("fetched_at", 0) > ttl:
            return None
        return data

    def _write_cache(self, path: str, body: Any, ttl: float | None) -> None:
        f = self._cache_file(path)
        if f is None or ttl == 0:
            return
        f.write_text(json.dumps({"path": path, "fetched_at": time.time(), "body": body}))

    # ---- endpoints ----------------------------------------------------------
    def state(self):
        return self.get("state/nfl", ttl=HOUR)

    def user(self, username_or_id: str):
        return self.get(f"user/{username_or_id}", ttl=DAY)

    def user_leagues(self, user_id: str, season: str | int):
        return self.get(f"user/{user_id}/leagues/nfl/{season}", ttl=DAY) or []

    def league(self, league_id: str, ttl=HOUR):
        return self.get(f"league/{league_id}", ttl=ttl)

    def league_users(self, league_id: str, ttl=DAY):
        return self.get(f"league/{league_id}/users", ttl=ttl) or []

    def rosters(self, league_id: str, ttl=HOUR):
        return self.get(f"league/{league_id}/rosters", ttl=ttl) or []

    def matchups(self, league_id: str, week: int, ttl=HOUR):
        return self.get(f"league/{league_id}/matchups/{week}", ttl=ttl) or []

    def transactions(self, league_id: str, week: int, ttl=HOUR):
        return self.get(f"league/{league_id}/transactions/{week}", ttl=ttl) or []

    def traded_picks(self, league_id: str, ttl=HOUR):
        return self.get(f"league/{league_id}/traded_picks", ttl=ttl) or []

    def winners_bracket(self, league_id: str, ttl=DAY):
        return self.get(f"league/{league_id}/winners_bracket", ttl=ttl) or []

    def losers_bracket(self, league_id: str, ttl=DAY):
        return self.get(f"league/{league_id}/losers_bracket", ttl=ttl) or []

    def league_drafts(self, league_id: str, ttl=DAY):
        return self.get(f"league/{league_id}/drafts", ttl=ttl) or []

    def draft(self, draft_id: str, ttl=DAY):
        return self.get(f"draft/{draft_id}", ttl=ttl)

    def draft_picks(self, draft_id: str, ttl=DAY):
        return self.get(f"draft/{draft_id}/picks", ttl=ttl) or []

    def draft_traded_picks(self, draft_id: str, ttl=DAY):
        return self.get(f"draft/{draft_id}/traded_picks", ttl=ttl) or []

    def players(self):
        # ~5 MB; Sleeper asks that this be pulled at most once a day.
        return self.get("players/nfl", ttl=DAY) or {}

    def trending(self, kind: str = "add", lookback_hours: int = 24, limit: int = 50):
        return (
            self.get(
                f"players/nfl/trending/{kind}?lookback_hours={lookback_hours}&limit={limit}",
                ttl=HOUR,
            )
            or []
        )

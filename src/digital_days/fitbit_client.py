"""Pull Fitbit wearable data for each participant into data/raw/fitbit/<pid>/.

STATUS (checked 30 Oct 2026 against https://developers.google.com/health):
  * Support for the legacy Fitbit Web API ended 30 Sep 2026; the legacy API and Fitbit OAuth
    endpoints are switched off on 2 Nov 2026. All new pulls must use the Google Health API.
  * Google Health API v4 (REST, https://health.googleapis.com) replaces 100+ legacy endpoints
    with data-type collections: users/me/dataTypes/{data_type}/dataPoints with list,
    rollUp (physical-time windows) and dailyRollUp (civil days).
  * Minute-level data IS available: heart-rate is stored at 1 s resolution and steps at 1 min,
    so wear time is computed from 60 s heart-rate rollUp windows (max 14-day range per call).
  * Auth is standard Google OAuth 2.0. Participants consent once in a browser with their
    Google account linked to the study Fitbit (see config/links.yaml -> google_health).

Classes
  DataSource       what the pipeline needs: sleep, daily steps, wear minutes per day
  GoogleHealthAPI  v4 implementation (writes native responses: gh_sleep.json, gh_steps_daily.json,
                   gh_wear_minutes.json; ingest.google_health_days reads them)
  FitbitWebAPI     legacy endpoints; kept ONLY to document/replay backfills pulled before 2 Nov 2026
  FitabaseExport   placeholder if the project licenses Fitabase instead

Credentials come from environment variables, never from the repository:
  GOOGLE_HEALTH_CLIENT_ID, GOOGLE_HEALTH_CLIENT_SECRET, GOOGLE_HEALTH_REDIRECT_URI,
  DD_TOKEN_STORE (folder outside the repo with one <pid>.json refresh-token file per participant).

Usage
  python -m digital_days.fitbit_client auth-url P001        # link to send/show the participant
  python -m digital_days.fitbit_client exchange P001 <code> # after consent, store refresh token
  python -m digital_days.fitbit_client pull P001 2028-01-10 2028-01-23
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

GH_BASE = "https://health.googleapis.com/v4"
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
# Read-only, least privilege: sleep, activity (steps) and health metrics (heart rate for wear time).
GH_SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]
LEGACY_SHUTDOWN = date(2026, 11, 2)


class DataSource(ABC):
    @abstractmethod
    def sleep(self, token: str, start: date, end: date) -> dict: ...

    @abstractmethod
    def steps(self, token: str, start: date, end: date) -> dict: ...

    @abstractmethod
    def wear_minutes(self, token: str, start: date, end: date) -> dict[str, int]: ...

    files = {"sleep": "sleep.json", "steps": "steps.json", "wear": "hr_minutes.json"}


# ---------------------------------------------------------------- Google Health API v4
def _civil(d: date) -> dict:
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


class GoogleHealthAPI(DataSource):
    """Google Health API v4. Endpoint and field names verified against the v4 discovery document
    (https://health.googleapis.com/$discovery/rest?version=v4) on 30 Oct 2026."""

    files = {"sleep": "gh_sleep.json", "steps": "gh_steps_daily.json", "wear": "gh_wear_minutes.json"}

    def __init__(self, tz: str = "Australia/Melbourne", session: requests.Session | None = None):
        self.tz = ZoneInfo(tz)
        self.http = session or requests.Session()

    def _call(self, method: str, path: str, token: str, **kw) -> dict:
        for attempt in range(6):                      # exponential backoff on 429/5xx (Google guidance)
            r = self.http.request(method, f"{GH_BASE}/{path}", timeout=60,
                                  headers={"Authorization": f"Bearer {token}"}, **kw)
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
                return r.json()
            time.sleep(2 ** attempt)
        r.raise_for_status()
        return {}

    def sleep(self, token, start, end):
        """Sleep sessions whose civil END date falls in [start, end] (page size is capped at 25)."""
        flt = (f'sleep.interval.civil_end_time >= "{start}" AND '
               f'sleep.interval.civil_end_time < "{end + timedelta(days=1)}"')
        out, page = [], None
        while True:
            params = {"filter": flt, "pageSize": 25, **({"pageToken": page} if page else {})}
            js = self._call("GET", "users/me/dataTypes/sleep/dataPoints", token, params=params)
            out += js.get("dataPoints", [])
            page = js.get("nextPageToken")
            if not page:
                return {"dataPoints": out}

    def steps(self, token, start, end):
        """Daily step totals by civil day (dailyRollUp; 90-day maximum range per call)."""
        out, page = [], None
        while True:
            body = {"range": {"start": _civil(start), "end": _civil(end + timedelta(days=1))},
                    "windowSizeDays": 1, **({"pageToken": page} if page else {})}
            js = self._call("POST", "users/me/dataTypes/steps/dataPoints:dailyRollUp", token, json=body)
            out += js.get("rollupDataPoints", [])
            page = js.get("nextPageToken")
            if not page:
                return {"rollupDataPoints": out}

    def wear_minutes(self, token, start, end):
        """Minutes per local calendar day with at least one heart-rate reading (60 s rollUp windows).

        Day boundaries are computed in the study timezone, so DST days have 23/25 hours.
        """
        out, d = {}, start
        while d <= end:
            lo = datetime(d.year, d.month, d.day, tzinfo=self.tz)
            hi = lo + timedelta(days=1)
            n, page = 0, None
            while True:
                body = {"range": {"startTime": lo.isoformat(), "endTime": hi.isoformat()},
                        "windowSize": "60s", "pageSize": 1500, **({"pageToken": page} if page else {})}
                js = self._call("POST", "users/me/dataTypes/heart-rate/dataPoints:rollUp", token, json=body)
                n += sum(1 for w in js.get("rollupDataPoints", [])
                         if (w.get("heartRate") or {}).get("beatsPerMinuteAvg") is not None)
                page = js.get("nextPageToken")
                if not page:
                    break
            out[str(d)] = n
            d += timedelta(days=1)
        return out

    def paired_devices(self, token) -> dict:
        """Which tracker is linked (used to check the study device ID matches the rotation schedule)."""
        return self._call("GET", "users/me/pairedDevices", token)


# ---------------------------------------------------------------- OAuth helpers (Google)
def auth_url(pid: str) -> str:
    """Consent link for one participant. `state` carries the study code (no personal data)."""
    q = {"client_id": os.environ["GOOGLE_HEALTH_CLIENT_ID"],
         "redirect_uri": os.environ["GOOGLE_HEALTH_REDIRECT_URI"],
         "response_type": "code", "scope": " ".join(GH_SCOPES),
         "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": pid}
    return f"{GOOGLE_AUTH}?{urllib.parse.urlencode(q)}"


def _store() -> Path:
    p = Path(os.environ.get("DD_TOKEN_STORE", "")).expanduser()
    if not str(p) or str(p) == "." or "digital-days" in str(p.resolve()):
        raise SystemExit("Set DD_TOKEN_STORE to a secure folder OUTSIDE the repository.")
    p.mkdir(parents=True, exist_ok=True)
    return p


def exchange(pid: str, code: str) -> None:
    r = requests.post(GOOGLE_TOKEN, timeout=60, data={
        "code": code, "client_id": os.environ["GOOGLE_HEALTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_HEALTH_CLIENT_SECRET"],
        "redirect_uri": os.environ["GOOGLE_HEALTH_REDIRECT_URI"], "grant_type": "authorization_code"})
    r.raise_for_status()
    tok = r.json()
    (_store() / f"{pid}.json").write_text(json.dumps({"refresh_token": tok["refresh_token"],
                                                       "scope": tok.get("scope"), "linked": str(date.today())}))


def access_token(pid: str) -> str:
    rt = json.loads((_store() / f"{pid}.json").read_text())["refresh_token"]
    r = requests.post(GOOGLE_TOKEN, timeout=60, data={
        "client_id": os.environ["GOOGLE_HEALTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_HEALTH_CLIENT_SECRET"],
        "refresh_token": rt, "grant_type": "refresh_token"})
    r.raise_for_status()
    return r.json()["access_token"]


# ---------------------------------------------------------------- legacy (switched off 2 Nov 2026)
class FitbitWebAPI(DataSource):
    """Legacy Fitbit Web API (dev.fitbit.com). Turned off by Google on 2 Nov 2026.
    Kept so that backfills pulled before the shutdown remain documented and re-readable."""
    BASE = "https://api.fitbit.com"

    def _get(self, token: str, path: str) -> dict:
        if date.today() >= LEGACY_SHUTDOWN:
            raise RuntimeError("The legacy Fitbit Web API was switched off on 2 Nov 2026; use GoogleHealthAPI.")
        r = requests.get(f"{self.BASE}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=60)
        r.raise_for_status()
        return r.json()

    def sleep(self, token, start, end):
        return self._get(token, f"/1.2/user/-/sleep/date/{start}/{end}.json")

    def steps(self, token, start, end):
        return self._get(token, f"/1/user/-/activities/steps/date/{start}/{end}.json")

    def wear_minutes(self, token, start, end):
        out = {}
        d = start
        while d <= end:  # intraday HR is one day per call
            js = self._get(token, f"/1/user/-/activities/heart/date/{d}/1d/1min.json")
            out[str(d)] = len(js.get("activities-heart-intraday", {}).get("dataset", []))
            d = date.fromordinal(d.toordinal() + 1)
        return out


class FitabaseExport:
    """Alternative if the project licenses Fitabase: drop its daily CSV exports in data/raw/fitabase/."""


def pull(source: DataSource, pid: str, token: str, start: date, end: date, out_root: Path) -> Path:
    out = Path(out_root) / pid
    out.mkdir(parents=True, exist_ok=True)
    (out / source.files["sleep"]).write_text(json.dumps(source.sleep(token, start, end)))
    (out / source.files["steps"]).write_text(json.dumps(source.steps(token, start, end)))
    (out / source.files["wear"]).write_text(json.dumps(source.wear_minutes(token, start, end)))
    return out


def main(argv: list[str]) -> None:
    if not os.environ.get("GOOGLE_HEALTH_CLIENT_ID"):
        raise SystemExit("Set GOOGLE_HEALTH_CLIENT_ID / _CLIENT_SECRET / _REDIRECT_URI and DD_TOKEN_STORE first "
                         "(see README > Fitbit via Google Health).")
    cmd, pid, *rest = argv
    if cmd == "auth-url":
        print(auth_url(pid))
    elif cmd == "exchange":
        exchange(pid, rest[0])
        print(f"{pid}: refresh token stored outside the repository.")
    elif cmd == "pull":
        from .common import ROOT, study
        start, end = date.fromisoformat(rest[0]), date.fromisoformat(rest[1])
        api = GoogleHealthAPI(tz=study()["study"]["timezone"])
        print(pull(api, pid, access_token(pid), start, end, ROOT / "data" / "raw" / "fitbit"))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

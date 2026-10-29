"""Pull Fitbit data for each participant into data/raw/fitbit/<pid>/.

IMPORTANT (Oct 2026): the legacy Fitbit Web API was reported deprecated in September 2026 in
favour of the Google Health API, with participants re-consenting through Google OAuth
(Momentum, Aug 2026; Fitabase, Mar 2026). Intraday (minute-level) access under the new API
was not confirmed at the time of writing. The code therefore separates:

  * DataSource interface   - what the pipeline needs (daily sleep, daily steps, wear minutes)
  * FitbitWebAPI           - legacy endpoints, kept for historical/backfill pulls while they work
  * GoogleHealthAPI        - placeholder: implement from Google's developer docs once the
                             project's Google Cloud app is registered (do NOT guess endpoints)
  * FitabaseExport         - alternative if the project licenses Fitabase

Credentials are read from environment variables, never from the repository:
  FITBIT_CLIENT_ID, FITBIT_CLIENT_SECRET, and a per-participant token store outside the repo.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

import requests


class DataSource(ABC):
    @abstractmethod
    def sleep(self, token: str, start: date, end: date) -> dict: ...

    @abstractmethod
    def steps(self, token: str, start: date, end: date) -> dict: ...

    @abstractmethod
    def wear_minutes(self, token: str, start: date, end: date) -> dict[str, int]: ...


class FitbitWebAPI(DataSource):
    """Legacy Fitbit Web API (dev.fitbit.com). Personal/Server app with 'Intraday' approval needed for HR minutes."""
    BASE = "https://api.fitbit.com"

    def _get(self, token: str, path: str) -> dict:
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


class GoogleHealthAPI(DataSource):
    """Successor API. Fill in from https://developers.google.com/health once the app is registered."""

    def sleep(self, token, start, end):
        raise NotImplementedError("Implement from Google Health API docs; verify sleep-stage and date semantics.")

    def steps(self, token, start, end):
        raise NotImplementedError("Implement from Google Health API docs.")

    def wear_minutes(self, token, start, end):
        raise NotImplementedError("Confirm whether minute-level heart rate is available; otherwise use hourly steps > 0.")


def pull(source: DataSource, pid: str, token: str, start: date, end: date, out_root: Path) -> Path:
    out = Path(out_root) / pid
    out.mkdir(parents=True, exist_ok=True)
    (out / "sleep.json").write_text(json.dumps(source.sleep(token, start, end)))
    (out / "steps.json").write_text(json.dumps(source.steps(token, start, end)))
    (out / "hr_minutes.json").write_text(json.dumps(source.wear_minutes(token, start, end)))
    return out


if __name__ == "__main__":
    if not os.environ.get("FITBIT_CLIENT_ID"):
        raise SystemExit("Set FITBIT_CLIENT_ID / FITBIT_CLIENT_SECRET and the token store path first.")

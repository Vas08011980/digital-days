"""Shared helpers: configuration loading, time handling, identifiers."""
from __future__ import annotations

from pathlib import Path
import hashlib

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"


def load_yaml(name: str) -> dict:
    with open(CONFIG / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def study() -> dict:
    return load_yaml("study.yaml")


def item_bank() -> dict:
    return load_yaml("item_bank.yaml")


def to_local(ts: pd.Series, tz: str | None = None) -> pd.Series:
    """Parse any timestamp column (epoch ms, epoch s, ISO strings) to tz-aware local time."""
    tz = tz or study()["study"]["timezone"]
    if pd.api.types.is_numeric_dtype(ts):
        unit = "ms" if ts.dropna().astype("int64").gt(10**11).all() else "s"
        out = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        out = pd.to_datetime(ts, utc=True, format="mixed")
    return out.dt.tz_convert(tz)


def study_day(local_ts: pd.Series, boundary_hour: int | None = None) -> pd.Series:
    """Calendar date of the 'study day' (default 04:00-03:59), so late-night use joins the evening before."""
    b = study()["study"]["day_boundary_hour"] if boundary_hour is None else boundary_hour
    return (local_ts - pd.Timedelta(hours=b)).dt.date


def pseudonymise(raw_id: str, salt: str) -> str:
    """Stable participant code. The salt lives with the coordinator, never in the repository."""
    return "DD" + hashlib.sha256((salt + str(raw_id)).encode()).hexdigest()[:8].upper()


def all_items(bank: dict | None = None) -> pd.DataFrame:
    """Flatten the item bank into one row per item with resolved response format."""
    bank = bank or item_bank()
    rsets = bank["response_sets"]
    rows = []
    for code, inst in bank["instruments"].items():
        for it in inst.get("items", []):
            resp = it.get("response") or rsets.get(it.get("response_set") or inst.get("response_set"), {})
            rows.append({
                "instrument": code,
                "item_id": it["id"],
                "text": it.get("text"),
                "status": it.get("status", "published"),
                "reverse": bool(it.get("reverse", False)),
                "parent": it.get("parent"),
                "licence": inst.get("licence", "check"),
                "resp_type": resp.get("type"),
                "resp_min": resp.get("min"),
                "resp_max": resp.get("max"),
                "min_label": resp.get("min_label"),
                "max_label": resp.get("max_label"),
                "options": "|".join(resp.get("options", [])) or None,
            })
    return pd.DataFrame(rows)

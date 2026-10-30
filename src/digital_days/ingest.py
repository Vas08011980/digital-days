"""Ingest raw streams into tidy, timestamped tables (one function per source).

Every function returns a DataFrame with at least: pid, ts_local (tz-aware), study_day.
Raw inputs are read from data/raw/<stream>/ (never committed; see .gitignore).

Sources and their native formats
  AWARE-Light   MySQL tables exported as CSV: esms, screen, applications_foreground, aware_device
  iOS           weekly Screen Time upload (form fields transcribed from the participant's screenshot)
  Fitbit        Google Health API v4 JSON (gh_*.json) pulled by fitbit_client.py; legacy Fitbit Web API
                JSON (sleep/steps/hr_minutes.json) for data pulled before the 2 Nov 2026 shutdown;
                or Fitabase CSV exports
  Donations     data download packages (DDPs) received via the AIO or the direct donation portal
                (docs/donate.html): ChatGPT and Claude conversations.json, TikTok user_data JSON,
                YouTube (Google Takeout) watch-history.json, or the portal's minimised
                digital_days_donation_<source>.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, study, study_day, to_local

AI_CATEGORIES = {"ai_chatbot", "ai_companion"}
SM_CATEGORIES = {"social_media", "short_video"}


def _finish(df: pd.DataFrame, ts_col: str = "ts_local") -> pd.DataFrame:
    df["study_day"] = study_day(df[ts_col])
    return df.sort_values(["pid", ts_col]).reset_index(drop=True)


def device_map(aware_device: pd.DataFrame) -> dict:
    """AWARE device_id -> study code. Participants join with their study code as the device label."""
    return dict(zip(aware_device["device_id"], aware_device["label"]))


def dedupe_aware(df: pd.DataFrame) -> pd.DataFrame:
    """AWARE can write duplicate rows with the same (device_id, timestamp) on some phones."""
    return df.drop_duplicates(subset=["device_id", "timestamp"] + [c for c in df.columns if c.startswith("esm_json")])


# ---------------------------------------------------------------- AWARE: ESM
def aware_esm(esms: pd.DataFrame, devices: dict, title_map: pd.DataFrame, gap_min: int = 30) -> pd.DataFrame:
    """Long ESM answers -> one row per prompt instance (wide by item_id).

    esm_status: 2 = answered; other statuses (dismissed/expired) count as a missed prompt.
    """
    e = dedupe_aware(esms.copy())
    e["pid"] = e["device_id"].map(devices)
    e = e.dropna(subset=["pid"])
    e["title"] = e["esm_json"].map(lambda s: json.loads(s).get("esm_title"))
    e["item_id"] = e["title"].map(dict(zip(title_map["esm_title"], title_map["item_id"])))
    e["ts_local"] = to_local(e["timestamp"])
    e["form"] = np.where(e["esm_trigger"].str.contains("Evening", case=False), "evening", "checkin")
    e = e.sort_values(["pid", "form", "ts_local"])
    # within each person x schedule, a new prompt instance starts when the gap to the previous
    # question exceeds gap_min (check-ins are >= 120 min apart by design)
    gap = e.groupby(["pid", "form"])["ts_local"].diff().dt.total_seconds().div(60)
    new = (gap.isna() | (gap > gap_min)).astype(int)
    e["prompt_seq"] = new.groupby([e["pid"], e["form"]]).cumsum()
    e["prompt_seq"] = e["form"].str[0] + e["prompt_seq"].astype(str)
    e["answered"] = e["esm_status"].eq(2)
    e["answer"] = pd.to_numeric(e["esm_user_answer"], errors="coerce").where(e["answered"])
    head = e.groupby(["pid", "prompt_seq"]).agg(ts_local=("ts_local", "min"), form=("form", "first"),
                                               answered=("answered", "any"))
    wide = e.pivot_table(index=["pid", "prompt_seq"], columns="item_id", values="answer", aggfunc="first")
    # non-numeric answers (radio/checkbox text) kept separately
    txt = e[pd.to_numeric(e["esm_user_answer"], errors="coerce").isna() & e["answered"]]
    if len(txt):
        wide = wide.join(txt.pivot_table(index=["pid", "prompt_seq"], columns="item_id",
                                         values="esm_user_answer", aggfunc="first"), rsuffix="_txt")
    return _finish(head.join(wide).reset_index())


# ---------------------------------------------------------------- AWARE: screen + apps
def aware_screen_sessions(screen: pd.DataFrame, devices: dict, max_session_min: int = 240) -> pd.DataFrame:
    """screen_status 3 (unlocked) ... next 0/2 (off/locked) -> unlocked sessions."""
    s = dedupe_aware(screen.copy())
    s["pid"] = s["device_id"].map(devices)
    s = s.dropna(subset=["pid"]).sort_values(["pid", "timestamp"])
    s["ts_local"] = to_local(s["timestamp"])
    s["next_ts"] = s.groupby("pid")["ts_local"].shift(-1)
    s["next_status"] = s.groupby("pid")["screen_status"].shift(-1)
    sess = s[(s["screen_status"] == 3) & s["next_status"].isin([0, 2])].copy()
    sess["minutes"] = (sess["next_ts"] - sess["ts_local"]).dt.total_seconds() / 60
    sess = sess[(sess["minutes"] > 0) & (sess["minutes"] <= max_session_min)]
    return _finish(sess[["pid", "ts_local", "next_ts", "minutes"]].rename(columns={"next_ts": "end_local"}))


def aware_app_use(apps: pd.DataFrame, devices: dict, categories: pd.DataFrame | None = None,
                  cap_min: int = 30) -> pd.DataFrame:
    """Foreground transitions -> app episodes with duration (time to next foreground event, capped)."""
    categories = categories if categories is not None else pd.read_csv(ROOT / "config" / "app_categories.csv")
    a = dedupe_aware(apps.copy())
    a["pid"] = a["device_id"].map(devices)
    a = a.dropna(subset=["pid"]).sort_values(["pid", "timestamp"])
    a["ts_local"] = to_local(a["timestamp"])
    a["minutes"] = (a.groupby("pid")["ts_local"].shift(-1) - a["ts_local"]).dt.total_seconds() / 60
    a["minutes"] = a["minutes"].clip(upper=cap_min).fillna(0)
    a = a.merge(categories[["package_name", "category"]], on="package_name", how="left")
    a["category"] = a["category"].fillna("other")
    return _finish(a[["pid", "ts_local", "package_name", "category", "minutes"]])


# ---------------------------------------------------------------- iOS
def ios_screentime(uploads: pd.DataFrame) -> pd.DataFrame:
    """Weekly Screen Time uploads -> per-day estimate (weekly total / 7), flagged as coarse.

    Expected columns: pid, week_ending (date), total_minutes, pickups, notifications,
    top1_app, top1_minutes ... (transcribed by the coordinator from the screenshot, double-entered).
    """
    rows = []
    for _, r in uploads.iterrows():
        end = pd.Timestamp(r["week_ending"])
        for d in range(7):
            rows.append({"pid": r["pid"], "study_day": (end - pd.Timedelta(days=d)).date(),
                         "screen_min_day": r["total_minutes"] / 7, "source": "ios_weekly"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- Fitbit
def _civil_date(cdt: dict):
    d = (cdt or {}).get("date") or {}
    return pd.Timestamp(year=int(d["year"]), month=int(d["month"]), day=int(d["day"])).date()


def google_health_days(pdir: Path) -> dict:
    """Google Health API v4 files written by fitbit_client.GoogleHealthAPI -> {date: {...}}.

    gh_sleep.json        {"dataPoints": [{"sleep": {...}}]}   (list endpoint, all pages merged)
    gh_steps_daily.json  {"rollupDataPoints": [...]}          (dailyRollUp, civil days)
    gh_wear_minutes.json {"YYYY-MM-DD": minutes}               (minutes with a heart-rate reading,
                                                               from 60 s rollUp windows per local day)
    Sleep is dated by its civil END date (the wake date), matching legacy `dateOfSleep`.
    v4 has no sleep-efficiency field; it is derived as minutesAsleep / minutesInSleepPeriod.
    """
    day: dict = {}
    f = pdir / "gh_sleep.json"
    if f.exists():
        for dp in json.loads(f.read_text()).get("dataPoints", []):
            s = dp.get("sleep") or {}
            meta = s.get("metadata") or {}
            if meta.get("nap") or meta.get("mainSleep") is False:
                continue
            iv, summ = s.get("interval") or {}, s.get("summary") or {}
            asleep = float(summ.get("minutesAsleep", "nan"))
            period = float(summ.get("minutesInSleepPeriod", "nan"))
            day.setdefault(_civil_date(iv.get("civilEndTime")), {}).update(
                sleep_min=asleep, sleep_eff=round(100 * asleep / period) if period > 0 else np.nan,
                sleep_start=iv.get("startTime"), sleep_end=iv.get("endTime"))
    f = pdir / "gh_steps_daily.json"
    if f.exists():
        for r in json.loads(f.read_text()).get("rollupDataPoints", []):
            if "steps" in r:
                day.setdefault(_civil_date(r.get("civilStartTime")), {})["steps"] = int(r["steps"].get("countSum", 0))
    f = pdir / "gh_wear_minutes.json"
    if f.exists():
        for d, m in json.loads(f.read_text()).items():
            day.setdefault(pd.Timestamp(d).date(), {})["wear_hours"] = m / 60
    return day


def legacy_fitbit_days(pdir: Path) -> dict:
    """Legacy Fitbit Web API files (sleep v1.2, activities-steps, intraday HR minute counts)."""
    day: dict = {}
    if (pdir / "sleep.json").exists():
        for s in json.loads((pdir / "sleep.json").read_text())["sleep"]:
            if not s.get("isMainSleep", True):
                continue
            d = pd.Timestamp(s["dateOfSleep"]).date()
            day.setdefault(d, {}).update(sleep_min=s["minutesAsleep"], sleep_eff=s.get("efficiency"),
                                         sleep_start=s["startTime"], sleep_end=s["endTime"])
    if (pdir / "steps.json").exists():
        for s in json.loads((pdir / "steps.json").read_text())["activities-steps"]:
            day.setdefault(pd.Timestamp(s["dateTime"]).date(), {})["steps"] = int(s["value"])
    if (pdir / "hr_minutes.json").exists():
        for d, m in json.loads((pdir / "hr_minutes.json").read_text()).items():
            day.setdefault(pd.Timestamp(d).date(), {})["wear_hours"] = m / 60
    return day


def fitbit_daily(raw_dir: Path) -> pd.DataFrame:
    """Per-participant wearable files -> daily rows (pid, study_day, sleep_min, sleep_eff, steps, wear_hours).

    Google Health API v4 files take precedence; legacy files fill dates v4 does not cover
    (e.g., backfills pulled before the legacy API was switched off on 2 Nov 2026).
    """
    rows = []
    for pdir in sorted(Path(raw_dir).glob("*")):
        if not pdir.is_dir():
            continue
        day = legacy_fitbit_days(pdir)
        for d, v in google_health_days(pdir).items():
            day.setdefault(d, {}).update(v)
        api = "google_health_v4" if list(pdir.glob("gh_*.json")) else "fitbit_legacy"
        for d, v in day.items():
            rows.append({"pid": pdir.name, "study_day": d, "wearable_api": api, **v})
    df = pd.DataFrame(rows)
    if len(df):
        min_h = study()["passive"]["wearable"]["min_wear_hours_valid_day"]
        df["wear_valid"] = df.get("wear_hours", pd.Series(np.nan, index=df.index)).ge(min_h)
    return df


# ---------------------------------------------------------------- Donations
def _words(text: str) -> int:
    return len(str(text).split())


def donation_chatgpt(path: Path, pid: str) -> pd.DataFrame:
    """ChatGPT export conversations.json -> one row per message (participant text kept; AI text dropped)."""
    rows = []
    for conv in json.loads(Path(path).read_text(encoding="utf-8")):
        for node in conv.get("mapping", {}).values():
            m = node.get("message")
            if not m or not m.get("create_time"):
                continue
            role = m["author"]["role"]
            if role not in ("user", "assistant"):
                continue
            parts = m.get("content", {}).get("parts") or []
            text = " ".join(p for p in parts if isinstance(p, str))
            rows.append({"pid": pid, "source": "chatgpt", "ts": m["create_time"], "role": "participant" if role == "user" else "ai",
                         "words": _words(text), "text": text if role == "user" else None,
                         "conversation_id": conv.get("id") or conv.get("conversation_id")})
    df = pd.DataFrame(rows)
    df["ts_local"] = to_local(df.pop("ts").astype(float))
    return _finish(df)


def donation_claude(path: Path, pid: str) -> pd.DataFrame:
    """Claude export conversations.json -> one row per message (participant text kept; AI text dropped)."""
    rows = []
    for conv in json.loads(Path(path).read_text(encoding="utf-8")):
        for m in conv.get("chat_messages", []):
            human = m.get("sender") == "human"
            text = m.get("text", "")
            rows.append({"pid": pid, "source": "claude", "ts": m["created_at"], "role": "participant" if human else "ai",
                         "words": _words(text), "text": text if human else None, "conversation_id": conv.get("uuid")})
    df = pd.DataFrame(rows)
    df["ts_local"] = to_local(df.pop("ts"))
    return _finish(df)


def donation_tiktok(path: Path, pid: str) -> pd.DataFrame:
    """TikTok user_data JSON -> video views. Handles both older and newer key layouts."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    act = data.get("Your Activity") or data.get("Activity") or {}
    hist = act.get("Watch History") or act.get("Video Browsing History") or {}
    vids = hist.get("VideoList") or []
    df = pd.DataFrame([{"pid": pid, "source": "tiktok", "event": "view", "ts": v["Date"]} for v in vids])
    df["ts_local"] = pd.to_datetime(df.pop("ts")).dt.tz_localize("UTC").dt.tz_convert(study()["study"]["timezone"])
    return _finish(df)


def donation_youtube(path: Path, pid: str) -> pd.DataFrame:
    """Google Takeout YouTube watch-history.json -> video views."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    df = pd.DataFrame([{"pid": pid, "source": "youtube", "event": "view", "ts": v["time"]}
                       for v in data if v.get("header") == "YouTube" and v.get("time")])
    df["ts_local"] = to_local(df.pop("ts"))
    return _finish(df)


def donation_portal(path: Path, pid: str) -> pd.DataFrame:
    """Minimised file produced in the participant's browser by docs/donate.html (outside the AIO).

    {"schema": "digital-days-donation/1", "source": "chatgpt|claude|tiktok|youtube|instagram",
     "records": [{"ts": ISO-8601 UTC, "kind": "message|view|like|search", "role": "participant|ai",
                  "words": int, "text": str|null}]}
    The browser has already dropped AI reply text and any item the participant chose to remove.
    """
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    recs = pd.DataFrame(d.get("records", []))
    if recs.empty:
        return recs
    recs["pid"], recs["source"] = pid, d["source"]
    recs["ts_local"] = to_local(recs.pop("ts"))
    if d["source"] in ("chatgpt", "claude"):
        recs["conversation_id"] = recs.get("conversation_id")
        return _finish(recs[["pid", "source", "role", "words", "text", "conversation_id", "ts_local"]])
    recs["event"] = recs.get("kind", "view")
    return _finish(recs[["pid", "source", "event", "ts_local"]])


DONATION_PARSERS = {
    "chatgpt": ("conversations.json", donation_chatgpt),
    "claude": ("conversations.json", donation_claude),
    "tiktok": ("user_data*.json", donation_tiktok),
    "youtube": ("**/watch-history.json", donation_youtube),
    # instagram: raw-DDP parser to be added once the AIO's processed output format is confirmed;
    # portal donations (digital_days_donation_instagram.json) are already handled below.
}
AI_SOURCES = ("chatgpt", "claude")


def donations(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """raw_dir/<pid>/<source>/<file> -> (ai_messages, media_views).

    Per source, a portal file (digital_days_donation_<source>.json) is used if present,
    otherwise the raw DDP file. One file per person x source.
    """
    ai, media = [], []
    for pdir in sorted(Path(raw_dir).glob("*")):
        sources = set(DONATION_PARSERS) | {"instagram"}
        for source in sorted(sources):
            portal = sorted((pdir / source).glob(f"digital_days_donation_{source}*.json"))
            if portal:
                df = donation_portal(portal[0], pdir.name)
            elif source in DONATION_PARSERS:
                pattern, fn = DONATION_PARSERS[source]
                files = sorted((pdir / source).glob(pattern))[:1]
                df = fn(files[0], pdir.name) if files else pd.DataFrame()
            else:
                df = pd.DataFrame()
            if not df.empty:
                (ai if source in AI_SOURCES else media).append(df)
    cat = lambda xs: pd.concat(xs, ignore_index=True) if xs else pd.DataFrame()
    return cat(ai), cat(media)

"""Align all streams on one timeline: moments within days within bursts within persons.

Outputs (data/processed/):
  moments.parquet/csv  one row per scheduled check-in (answered or missed) + passive windows
  days.parquet/csv     one row per person-day: evening report + daily passive, Fitbit, donation aggregates
  ai_messages.parquet  participant AI-chat text for WP2.2 (secure storage only; never exported)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .ingest import AI_CATEGORIES, SM_CATEGORIES

COMPOSITES = {
    # name: (items, reversed items, scale max)
    "mood_m": (["MOOD_M1", "MOOD_M2"], ["MOOD_M3"], 7),
    "lonely_m": (["LONE_M1", "LONE_M2"], ["LONE_M3"], 7),
    "stress_m": (["STRESS_M1", "STRESS_M2"], ["STRESS_M3"], 7),
    "mood_d": (["MOOD_D1", "MOOD_D2"], ["MOOD_D3"], 7),
    "ls_d": (["LS_D1", "LS_D2", "LS_D3"], [], 7),
    "se_d": (["SE_D1", "SE_D2"], ["SE_D3"], 7),
    "presence_d": (["PRES_D1", "PRES_D2", "PRES_D3"], [], 7),
    "flow_d": (["FLOW_D1", "FLOW_D2", "FLOW_D3"], [], 7),
    "selfrep_d": (["SREP_D1", "SREP_D2", "SREP_D3"], [], 7),
}


def score(df: pd.DataFrame) -> pd.DataFrame:
    """Composite = mean of available items (reverse-keyed as max+1-x); NaN if fewer than 2 items answered."""
    for name, (pos, rev, mx) in COMPOSITES.items():
        cols = [c for c in pos + rev if c in df]
        if not cols:
            continue
        parts = [df[c] for c in pos if c in df] + [mx + 1 - df[c] for c in rev if c in df]
        m = pd.concat(parts, axis=1)
        df[name] = m.mean(axis=1).where(m.notna().sum(axis=1) >= 2)
    return df


def assign_bursts(df: pd.DataFrame, bursts: pd.DataFrame) -> pd.DataFrame:
    """bursts: pid, burst, start (date), end (date). Rows outside a burst window get burst = NaN."""
    df = df.copy()
    df["study_day"] = pd.to_datetime(df["study_day"])
    b = bursts.assign(start=pd.to_datetime(bursts["start"]), end=pd.to_datetime(bursts["end"]))
    m = df.merge(b, on="pid", how="left")
    inside = (m["study_day"] >= m["start"]) & (m["study_day"] <= m["end"])
    m = m[inside].copy()
    m["day_in_burst"] = (m["study_day"] - m["start"]).dt.days + 1
    return m.drop(columns=["start", "end"])


def _window_minutes(prompts: pd.DataFrame, episodes: pd.DataFrame, minutes: int, label: str) -> pd.Series:
    """Minutes of `episodes` in the `minutes` before each prompt (episode start inside window)."""
    out = pd.Series(0.0, index=prompts.index)
    if episodes is None or episodes.empty:
        return out * np.nan
    for pid, g in prompts.groupby("pid"):
        e = episodes[episodes["pid"] == pid]
        if e.empty:
            out.loc[g.index] = np.nan          # no passive data for this person (e.g., iOS)
            continue
        t = e["ts_local"].values
        mins = e["minutes"].values
        for idx, ts in g["ts_local"].items():
            lo = ts - pd.Timedelta(minutes=minutes)
            sel = (t >= lo.to_datetime64()) & (t < ts.to_datetime64())
            out.loc[idx] = mins[sel].sum()
    return out.rename(label)


def daily_passive(sessions: pd.DataFrame, apps: pd.DataFrame, ios: pd.DataFrame | None) -> pd.DataFrame:
    parts = []
    if sessions is not None and not sessions.empty:
        s = sessions.copy()
        hr = s["ts_local"].dt.hour
        parts.append(s.groupby(["pid", "study_day"]).agg(screen_min_day=("minutes", "sum")))
        night = s[(hr >= 0) & (hr < 5)]
        parts.append(night.groupby(["pid", "study_day"]).agg(night_use_min=("minutes", "sum")))
    if apps is not None and not apps.empty:
        a = apps.copy()
        parts.append(a[a["category"].isin(SM_CATEGORIES)].groupby(["pid", "study_day"]).agg(sm_min_day=("minutes", "sum")))
        parts.append(a[a["category"].isin(AI_CATEGORIES)].groupby(["pid", "study_day"]).agg(ai_app_min_day=("minutes", "sum")))
    out = pd.concat(parts, axis=1).reset_index() if parts else pd.DataFrame(columns=["pid", "study_day"])
    out["passive_source"] = "android"
    if ios is not None and not ios.empty:
        out = pd.concat([out, ios.assign(passive_source="ios_weekly")], ignore_index=True)
    out["study_day"] = pd.to_datetime(out["study_day"])
    return out


def daily_donations(ai: pd.DataFrame, media: pd.DataFrame) -> pd.DataFrame:
    parts = []
    if ai is not None and not ai.empty:
        p = ai[ai["role"] == "participant"].copy()
        p["late"] = p["ts_local"].dt.hour.isin([23, 0, 1, 2, 3])
        parts.append(p.groupby(["pid", "study_day"]).agg(ai_msgs_day=("words", "size"), ai_words_day=("words", "sum"),
                                                         ai_late_msgs=("late", "sum")))
    if media is not None and not media.empty:
        v = media.groupby(["pid", "study_day", "source"]).size().unstack("source")
        v.columns = [f"{c}_views_day" for c in v.columns]
        parts.append(v)
    if not parts:
        return pd.DataFrame(columns=["pid", "study_day"])
    out = pd.concat(parts, axis=1).reset_index()
    out["study_day"] = pd.to_datetime(out["study_day"])
    return out


def build(esm, sessions, apps, ios, fitbit, ai, media, bursts):
    esm = score(esm.copy())
    esm = assign_bursts(esm, bursts)

    # ---- moments (check-ins)
    mom = esm[esm["form"] == "checkin"].copy()
    mom["screen_min_prev60"] = _window_minutes(mom, sessions, 60, "screen_min_prev60")
    ai_eps = apps[apps["category"].isin(AI_CATEGORIES)] if apps is not None and not apps.empty else None
    mom["ai_min_prev60"] = _window_minutes(mom, ai_eps, 60, "ai_min_prev60")
    mom["prompt_in_day"] = mom.groupby(["pid", "study_day"]).cumcount() + 1

    # ---- days (evening report + daily aggregates)
    eve = esm[esm["form"] == "evening"].drop(columns=["form", "prompt_seq"], errors="ignore")
    keys = ["pid", "study_day"]
    day = eve.drop_duplicates(keys).set_index(keys)
    dp = daily_passive(sessions, apps, ios)
    don = daily_donations(ai, media)
    fb = fitbit.copy() if fitbit is not None and not fitbit.empty else pd.DataFrame(columns=keys)
    fb["study_day"] = pd.to_datetime(fb["study_day"])
    # sleep belongs to the night BEFORE the study day it ends on: Fitbit dateOfSleep = wake date = study day
    for extra in (dp, don, fb):
        if not extra.empty:
            day = day.join(extra.drop_duplicates(keys).set_index(keys), how="left", rsuffix="_dup")
    day = day.reset_index()
    day = day.drop(columns=[c for c in day if c.endswith(("_M1", "_M2", "_M3")) or c in
                            ("mood_m", "lonely_m", "stress_m", "source")], errors="ignore")
    # donors: a day with no donated messages/views is a true zero, not missing
    for frame, cols in ((ai, ["ai_msgs_day", "ai_words_day", "ai_late_msgs"]),
                        (media, [c for c in day if c.endswith("_views_day")])):
        if frame is not None and not frame.empty:
            donors = day["pid"].isin(frame["pid"].unique())
            for c in cols:
                if c in day:
                    day.loc[donors, c] = day.loc[donors, c].fillna(0)
    # within-person lagged predictors used in WP2.1 (e.g., last night's late AI use -> today's mood)
    day = day.sort_values(keys)
    for col in ("ai_late_msgs", "night_use_min", "sleep_min"):
        if col in day:
            day[f"{col}_lag1"] = day.groupby(["pid", "burst"])[col].shift(1)
    return mom.reset_index(drop=True), day

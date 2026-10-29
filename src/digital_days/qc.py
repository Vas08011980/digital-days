"""Quality control report per person x burst (written to data/processed/qc_person_burst.csv).

compliance      answered prompts / scheduled prompts (design: 3 check-ins + 1 evening per day)
low_compliance  < 33% (flag for sensitivity analyses, not exclusion by default)
straightline    evening reports where all 1-7 items take one value
wear_valid_days Fitbit days with >= min_wear_hours
passive_days    days with any phone-use data (Android sensing or iOS weekly upload)
donation        whether any AI-chat or media donation was received
"""
from __future__ import annotations

import pandas as pd

from .common import study

LIKERT_EVENING = ["MOOD_D1", "MOOD_D2", "MOOD_D3", "LS_D1", "LS_D2", "LS_D3", "SE_D1", "SE_D2", "SE_D3",
                  "PRES_D1", "PRES_D2", "PRES_D3", "FLOW_D1", "FLOW_D2", "FLOW_D3", "SREP_D1", "SREP_D2", "SREP_D3"]


def report(moments: pd.DataFrame, days: pd.DataFrame) -> pd.DataFrame:
    s = study()
    per_day = s["ema"]["checkins"]["per_day"] + s["ema"]["evening"]["per_day"]
    scheduled = per_day * s["bursts"]["length_days"]
    m = moments.groupby(["pid", "burst"])["answered"].sum().rename("checkins_answered")
    e = days.groupby(["pid", "burst"])["answered"].sum().rename("evenings_answered")
    q = pd.concat([m, e], axis=1).fillna(0)
    q["scheduled"] = scheduled
    q["compliance"] = (q["checkins_answered"] + q["evenings_answered"]) / scheduled
    q["low_compliance"] = q["compliance"] < 0.33
    cols = [c for c in LIKERT_EVENING if c in days]
    if cols:
        sl = days[cols].nunique(axis=1).eq(1) & days[cols].notna().all(axis=1)
        q["straightline_evenings"] = sl.groupby([days["pid"], days["burst"]]).sum()
    if "wear_valid" in days:
        q["wear_valid_days"] = days.groupby(["pid", "burst"])["wear_valid"].sum()
    pas = [c for c in ("screen_min_day",) if c in days]
    if pas:
        q["passive_days"] = days[pas[0]].notna().groupby([days["pid"], days["burst"]]).sum()
    don = [c for c in days if c.endswith("_views_day") or c == "ai_msgs_day"]
    if don:
        q["donation_received"] = days[don].notna().any(axis=1).groupby([days["pid"], days["burst"]]).any()
    return q.reset_index()


def summary(q: pd.DataFrame) -> dict:
    return {
        "person_bursts": len(q),
        "mean_compliance": round(q["compliance"].mean(), 3),
        "low_compliance_share": round(q["low_compliance"].mean(), 3),
        "mean_wear_valid_days": round(q.get("wear_valid_days", pd.Series(dtype=float)).mean(), 1),
        "donation_rate": round(q.get("donation_received", pd.Series(dtype=float)).mean(), 3),
    }

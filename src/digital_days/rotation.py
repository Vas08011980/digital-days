"""Fitbit rotation scheduler: can ~100 active devices cover every participant in every burst?

Each participant needs a device for the 14-day burst plus a shipping/charging/reset buffer.
Participants are enrolled in staggered waves; this assigns device IDs greedily and reports
the calendar span each burst round needs (must fit well inside the 6-month gap).
"""
from __future__ import annotations

import pandas as pd

from .common import study


def schedule(n_participants: int | None = None, start: str = "2028-01-10") -> tuple[pd.DataFrame, dict]:
    s = study()
    n = n_participants or s["sample"]["enrol_target"]
    w = s["passive"]["wearable"]
    burst_len, buffer = s["bursts"]["length_days"], w["shipping_buffer_days"]
    n_dev = w["devices_active"]
    slot = burst_len + buffer
    rows = []
    for b in range(s["bursts"]["n"]):
        round_start = pd.Timestamp(start) + pd.DateOffset(months=s["bursts"]["spacing_months"] * b)
        for i in range(n):
            cohort = i // n_dev                 # participants in the same cohort share a start date
            rows.append({"pid": f"P{i+1:03d}", "burst": b + 1, "device": f"FB{(i % n_dev) + 1:03d}",
                         "start": round_start + pd.Timedelta(days=cohort * slot),
                         "end": round_start + pd.Timedelta(days=cohort * slot + burst_len - 1)})
    df = pd.DataFrame(rows)
    span = (df.groupby("burst")["end"].max() - df.groupby("burst")["start"].min()).dt.days + 1
    summary = {
        "participants": n, "active_devices": n_dev, "spares": w["devices_spare"],
        "cohorts_per_round": -(-n // n_dev),
        "days_per_round": int(span.max()),
        "fits_in_gap": bool(span.max() < s["bursts"]["spacing_months"] * 30 - 30),
        "device_overlap": bool(df.sort_values("start").groupby(["burst", "device"]).apply(
            lambda g: (g["start"].iloc[1:].values <= g["end"].iloc[:-1].values).any(), include_groups=False).any()),
    }
    return df, summary


if __name__ == "__main__":
    _, s = schedule()
    print(s)

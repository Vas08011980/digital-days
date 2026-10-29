"""End-to-end runner.

  python -m digital_days.run --simulate        # build configs, simulate raw data, run pipeline, check recovery
  python -m digital_days.run --raw data/raw    # run on real exports (same folder layout as the simulation)

Folder layout expected under --raw:
  aware/{aware_device,esms,screen,applications_foreground}.csv
  ios/screentime_uploads.csv
  fitbit/<pid>/{sleep,steps,hr_minutes}.json
  donations/<pid>/{chatgpt,claude,tiktok,youtube}/<export files>
  bursts.csv  (pid, burst, start, end)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import align, build_aware_config, export_forms, ingest, qc, rotation, simulate
from .common import ROOT


def _read(p: Path) -> pd.DataFrame:
    return pd.read_csv(p) if p.exists() and p.stat().st_size > 1 else pd.DataFrame()


def pipeline(raw: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    devices = ingest.device_map(_read(raw / "aware" / "aware_device.csv"))
    title_map = pd.read_csv(ROOT / "build" / "aware" / "esm_title_map.csv")
    esm = ingest.aware_esm(_read(raw / "aware" / "esms.csv"), devices, title_map)
    scr = _read(raw / "aware" / "screen.csv")
    sessions = ingest.aware_screen_sessions(scr, devices) if len(scr) else pd.DataFrame()
    app = _read(raw / "aware" / "applications_foreground.csv")
    apps = ingest.aware_app_use(app, devices) if len(app) else pd.DataFrame()
    ios_raw = _read(raw / "ios" / "screentime_uploads.csv")
    ios = ingest.ios_screentime(ios_raw) if len(ios_raw) else pd.DataFrame()
    fit = ingest.fitbit_daily(raw / "fitbit")
    ai, media = ingest.donations(raw / "donations")
    bursts = pd.read_csv(raw / "bursts.csv")

    moments, days = align.build(esm, sessions, apps, ios, fit, ai, media, bursts)
    q = qc.report(moments, days)

    moments.to_csv(out / "moments.csv", index=False)
    days.to_csv(out / "days.csv", index=False)
    q.to_csv(out / "qc_person_burst.csv", index=False)
    if not ai.empty:   # participant text: secure storage only, never leaves the RMIT environment
        ai.to_csv(out / "ai_messages_SECURE.csv", index=False)
    mplus_export(moments, days, out)
    return {"moments": len(moments), "days": len(days), "persons": days["pid"].nunique(), **qc.summary(q)}


def mplus_export(moments: pd.DataFrame, days: pd.DataFrame, out: Path) -> None:
    """Numeric, no-header .dat files + variable lists for DSEM (missing = -999)."""
    pid_num = {p: i + 1 for i, p in enumerate(sorted(days["pid"].unique()))}
    for name, df, cols in [
        ("days", days, ["burst", "day_in_burst", "mood_d", "ls_d", "se_d", "presence_d", "flow_d", "selfrep_d",
                        "ai_late_msgs_lag1", "night_use_min_lag1", "sleep_min", "steps", "screen_min_day",
                        "ai_msgs_day", "tiktok_views_day", "youtube_views_day"]),
        ("moments", moments, ["burst", "day_in_burst", "prompt_in_day", "mood_m", "lonely_m", "stress_m",
                              "screen_min_prev60", "ai_min_prev60"]),
    ]:
        cols = [c for c in cols if c in df]
        d = df[["pid"] + cols].copy()
        d.insert(0, "id", d.pop("pid").map(pid_num))
        d.apply(pd.to_numeric, errors="coerce").fillna(-999).to_csv(out / f"mplus_{name}.dat", sep=" ",
                                                                       header=False, index=False, float_format="%.4f")
        (out / f"mplus_{name}_names.txt").write_text("NAMES = " + " ".join(["id"] + cols) + ";\nMISSING = ALL(-999);\n")


def recovery_check(days: pd.DataFrame) -> dict:
    """Does the pipeline recover the planted within-person effect? (two-level model, person-mean centred)."""
    import statsmodels.formula.api as smf
    d = days.dropna(subset=["mood_d", "ai_late_msgs_lag1"]).copy()
    d["x_w"] = d["ai_late_msgs_lag1"] - d.groupby("pid")["ai_late_msgs_lag1"].transform("mean")
    d["x_w"] = d["x_w"] / d["x_w"].std()
    d["y"] = (d["mood_d"] - d["mood_d"].mean()) / d.groupby("pid")["mood_d"].transform(lambda s: s - s.mean()).std()
    fit = smf.mixedlm("y ~ x_w", d, groups=d["pid"]).fit()
    return {"beta_within": round(float(fit.params["x_w"]), 3), "p": float(fit.pvalues["x_w"]), "n_days": len(d),
            "planted": simulate.PLANTED_BETA}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--out", default=str(ROOT / "data" / "processed"))
    a = ap.parse_args()
    build_aware_config.main()
    export_forms.main()
    _, rot = rotation.schedule()
    print("Fitbit rotation:", rot)
    raw = Path(a.raw)
    if a.simulate:
        raw = simulate.run(ROOT / "data" / "simulated" / "raw", n=a.n)
        a.out = str(ROOT / "data" / "simulated" / "processed")
    res = pipeline(raw, Path(a.out))
    print("Pipeline:", json.dumps(res, default=str))
    if a.simulate:
        rc = recovery_check(pd.read_csv(Path(a.out) / "days.csv"))
        print("Recovery check:", rc)
        (Path(a.out) / "recovery_check.json").write_text(json.dumps({**res, **rc}, indent=2, default=str))


if __name__ == "__main__":
    main()

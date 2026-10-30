"""Simulate participants and write every raw stream in its NATIVE format, so the whole
pipeline (ingest -> align -> QC -> analysis export) can be tested end to end before ethics.

A known within-person effect is planted:
    late-night AI chatting on study day d (23:00-04:00) lowers evening mood on day d+1
    (standardised beta = PLANTED_BETA). The end-to-end test checks the pipeline recovers it.

Simplifications (documented, not hidden):
  * every participant answers EMA through the AWARE ESM format (in the real study iOS users
    use the cross-platform EMA app, whose export is mapped to the same long format);
  * Fitbit files mimic Google Health API v4 responses (sleep list, steps dailyRollUp, and the
    per-day wear-minute counts the client derives from heart-rate rollUp); fitbit_api="legacy"
    writes the old Fitbit Web API v1.2 shapes instead;
  * donation files contain only the fields the parsers read; Instagram arrives as a
    portal-minimised file (docs/donate.html), the others as raw DDPs.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import study
from .rotation import schedule

PLANTED_BETA = -0.30
TZ = "Australia/Melbourne"


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.tz_convert("UTC").value // 1_000_000)


def _civil(ts: pd.Timestamp) -> dict:
    return {"date": {"year": ts.year, "month": ts.month, "day": ts.day},
            "time": {"hours": ts.hour, "minutes": ts.minute}}


def _offset(ts: pd.Timestamp) -> str:
    return f"{int(ts.utcoffset().total_seconds())}s"


def gh_sleep_point(start: pd.Timestamp, end: pd.Timestamp, asleep: int) -> dict:
    """One Google Health API v4 sleep data point (fields as in the v4 discovery document)."""
    period = int((end - start).total_seconds() // 60)
    return {"sleep": {
        "interval": {"startTime": start.tz_convert("UTC").isoformat().replace("+00:00", "Z"),
                     "endTime": end.tz_convert("UTC").isoformat().replace("+00:00", "Z"),
                     "startUtcOffset": _offset(start), "endUtcOffset": _offset(end),
                     "civilStartTime": _civil(start), "civilEndTime": _civil(end)},
        "type": "STAGES",
        "metadata": {"processed": True, "mainSleep": True, "nap": False, "stagesStatus": "SUCCEEDED"},
        "summary": {"minutesAsleep": str(asleep), "minutesInSleepPeriod": str(period),
                    "minutesAwake": str(period - asleep)}},
        "dataSource": {"platform": "FITBIT", "recordingMethod": "PASSIVELY_MEASURED"}}


def run(out: Path, n: int = 60, n_bursts: int = 2, seed: int = 2027, fitbit_api: str = "google_health") -> Path:
    rng = np.random.default_rng(seed)
    s = study()
    out = Path(out)
    for d in ("aware", "ios", "fitbit", "donations"):
        (out / d).mkdir(parents=True, exist_ok=True)

    sched, _ = schedule(n_participants=n)
    sched = sched[sched["burst"] <= n_bursts].copy()
    sched[["pid", "burst", "start", "end"]].assign(
        start=lambda d: d["start"].dt.date, end=lambda d: d["end"].dt.date).to_csv(out / "bursts.csv", index=False)

    title_map = pd.read_csv(Path(__file__).resolve().parents[2] / "build" / "aware" / "esm_title_map.csv")
    t2i = dict(zip(title_map["item_id"], title_map["esm_title"]))

    devices, esms, screen, apps, ios_rows = [], [], [], [], []
    ai_pkgs = ["com.openai.chatgpt", "com.anthropic.claude"]
    other_pkgs = ["com.instagram.android", "com.zhiliaoapp.musically", "com.whatsapp", "com.google.android.youtube",
                  "com.android.chrome", "com.spotify.music"]

    for i in range(n):
        pid = f"P{i+1:03d}"
        android = rng.random() < 0.55
        dev = f"dev-{i:04d}"
        devices.append({"device_id": dev, "label": pid})   # iOS: EMA still flows through the same format
        person_mood = rng.normal(0, 0.8)
        late_rate = rng.gamma(1.2, 1.5)          # person's typical late-night AI messages
        compliance = np.clip(rng.beta(6, 4), 0.2, 0.95)
        sleep_mean = rng.normal(430, 40)
        steps_mean = rng.normal(8000, 2500)
        sleeps, steps, hr = [], [], {}
        gh_sleep, gh_steps = [], []
        convs_gpt, convs_claude, tiktok, yt, insta = [], [], [], [], []
        for _, b in sched[sched["pid"] == pid].iterrows():
            days = pd.date_range(b["start"], b["end"], freq="D")
            late_prev = None
            for d in days:
                late = rng.poisson(late_rate)
                late_z = (late_prev - late_rate) / max(np.sqrt(late_rate), 0.5) if late_prev is not None else 0.0
                mood_true = person_mood + PLANTED_BETA * late_z + rng.normal(0, 0.7)
                late_prev = late
                day0 = pd.Timestamp(d).tz_localize(TZ)
                # --- check-ins
                for k, hour in enumerate(sorted(rng.choice([9, 11, 13, 15, 17, 19], 3, replace=False))):
                    ts = day0 + pd.Timedelta(hours=int(hour), minutes=int(rng.integers(0, 59)))
                    answered = rng.random() < compliance
                    for item in ["MOOD_M1", "MOOD_M2", "MOOD_M3", "LONE_M1", "LONE_M2", "LONE_M3",
                                 "STRESS_M1", "STRESS_M2", "STRESS_M3"]:
                        # positive-keyed mood items rise with mood; loneliness/stress items fall;
                        # the reverse-keyed third item of each block flips direction
                        direction = 1 if item.startswith("MOOD") else -1
                        if item.endswith("3"):
                            direction *= -1
                        val = int(np.clip(round(4 + direction * mood_true + rng.normal(0, 0.8)), 1, 7))
                        esms.append({"device_id": dev, "timestamp": _ms(ts), "esm_trigger": "Check-in",
                                     "esm_json": json.dumps({"esm_title": t2i[item]}),
                                     "esm_status": 2 if answered else 3,
                                     "esm_user_answer": val if answered else ""})
                        ts += pd.Timedelta(seconds=5)
                # --- evening report (20:00)
                ts = day0 + pd.Timedelta(hours=20, minutes=int(rng.integers(0, 50)))
                answered = rng.random() < compliance
                for item in ["MOOD_D1", "MOOD_D2", "MOOD_D3", "LS_D1", "LS_D2", "LS_D3", "SE_D1", "SE_D2", "SE_D3",
                             "PRES_D1", "PRES_D2", "PRES_D3", "FLOW_D1", "FLOW_D2", "FLOW_D3",
                             "SREP_D1", "SREP_D2", "SREP_D3", "SLEEP_D1", "SLEEP_D2", "AI_D1"]:
                    if item in ("MOOD_D3", "SE_D3"):
                        val = int(np.clip(round(4 - mood_true + rng.normal(0, 0.6)), 1, 7))
                    elif item.startswith(("MOOD", "LS", "SE")):
                        val = int(np.clip(round(4 + mood_true + rng.normal(0, 0.6)), 1, 7))
                    elif item == "SLEEP_D1":
                        val = round(rng.normal(sleep_mean / 60, 0.8), 1)
                    elif item == "SLEEP_D2":
                        val = int(np.clip(round(rng.normal(6, 2)), 0, 10))
                    elif item == "AI_D1":
                        val = int(max(0, rng.normal(late * 4 + 15, 10)))
                    else:
                        val = int(rng.integers(1, 8))
                    esms.append({"device_id": dev, "timestamp": _ms(ts), "esm_trigger": "Evening report",
                                 "esm_json": json.dumps({"esm_title": t2i[item]}),
                                 "esm_status": 2 if answered else 3, "esm_user_answer": val if answered else ""})
                    ts += pd.Timedelta(seconds=6)
                # --- late-night AI messages (donated), 23:00-03:59 belongs to study day d
                for _ in range(late):
                    mts = day0 + pd.Timedelta(hours=23) + pd.Timedelta(minutes=int(rng.integers(0, 299)))
                    msg = {"text": "cant sleep again, can we talk for a bit", "ts": mts}
                    (convs_gpt if rng.random() < 0.6 else convs_claude).append(msg)
                for _ in range(rng.poisson(3)):  # daytime AI use
                    mts = day0 + pd.Timedelta(hours=int(rng.integers(9, 18)), minutes=int(rng.integers(0, 59)))
                    (convs_gpt if rng.random() < 0.6 else convs_claude).append({"text": "help me plan my essay", "ts": mts})
                for _ in range(rng.poisson(40)):
                    tiktok.append(day0 + pd.Timedelta(minutes=int(rng.integers(8 * 60, 26 * 60))))
                for _ in range(rng.poisson(6)):
                    yt.append(day0 + pd.Timedelta(minutes=int(rng.integers(8 * 60, 24 * 60))))
                for _ in range(rng.poisson(10)):
                    insta.append(day0 + pd.Timedelta(minutes=int(rng.integers(8 * 60, 24 * 60))))
                # --- phone sensing (Android only)
                if android:
                    for _ in range(rng.poisson(25)):
                        on = day0 + pd.Timedelta(minutes=int(rng.integers(7 * 60, 26 * 60)))
                        dur = float(rng.gamma(1.5, 4))
                        screen += [{"device_id": dev, "timestamp": _ms(on), "screen_status": 3},
                                   {"device_id": dev, "timestamp": _ms(on + pd.Timedelta(minutes=dur)), "screen_status": 2}]
                        pkg = ai_pkgs[int(rng.integers(0, 2))] if rng.random() < 0.12 else other_pkgs[int(rng.integers(0, 6))]
                        apps.append({"device_id": dev, "timestamp": _ms(on + pd.Timedelta(seconds=2)), "package_name": pkg})
                        apps.append({"device_id": dev, "timestamp": _ms(on + pd.Timedelta(minutes=dur)),
                                     "package_name": "com.android.systemui"})
                # --- Fitbit (sleep ending on day d)
                if rng.random() < 0.9:
                    mins = int(rng.normal(sleep_mean, 45))
                    wake = day0 + pd.Timedelta(hours=7)
                    bed = wake - pd.Timedelta(minutes=mins + int(rng.integers(20, 60)))
                    gh_sleep.append(gh_sleep_point(bed, wake, mins))
                    sleeps.append({"dateOfSleep": str(d.date()), "isMainSleep": True, "minutesAsleep": mins,
                                   "efficiency": int(np.clip(rng.normal(90, 4), 60, 100)),
                                   "startTime": str(bed.isoformat()), "endTime": str(wake.isoformat())})
                    n_steps = int(max(0, rng.normal(steps_mean, 2000)))
                    gh_steps.append({"civilStartTime": {"date": {"year": d.year, "month": d.month, "day": d.day}},
                                     "civilEndTime": _civil(day0 + pd.Timedelta(days=1)),
                                     "steps": {"countSum": str(n_steps)}})
                    steps.append({"dateTime": str(d.date()), "value": str(n_steps)})
                    hr[str(d.date())] = int(np.clip(rng.normal(20 * 60, 180), 0, 1440))
            if not android:
                for wk in pd.date_range(b["start"], b["end"], freq="7D"):
                    ios_rows.append({"pid": pid, "week_ending": (wk + pd.Timedelta(days=6)).date(),
                                     "total_minutes": int(rng.normal(7 * 300, 300)), "pickups": int(rng.normal(500, 100))})
        # write per-person files
        fdir = out / "fitbit" / pid
        fdir.mkdir(parents=True, exist_ok=True)
        if fitbit_api == "legacy":
            (fdir / "sleep.json").write_text(json.dumps({"sleep": sleeps}))
            (fdir / "steps.json").write_text(json.dumps({"activities-steps": steps}))
            (fdir / "hr_minutes.json").write_text(json.dumps(hr))
        else:   # Google Health API v4 (newest-first ordering, as the list endpoint returns it)
            (fdir / "gh_sleep.json").write_text(json.dumps({"dataPoints": gh_sleep[::-1]}))
            (fdir / "gh_steps_daily.json").write_text(json.dumps({"rollupDataPoints": gh_steps}))
            (fdir / "gh_wear_minutes.json").write_text(json.dumps(hr))
        if rng.random() < 0.8:  # donors
            ddir = out / "donations" / pid
            for src in ("chatgpt", "claude", "tiktok", "youtube", "instagram"):
                (ddir / src).mkdir(parents=True, exist_ok=True)
            mapping = {}
            for j, m in enumerate(convs_gpt):
                t = m["ts"].tz_convert("UTC").timestamp()
                mapping[f"u{j}"] = {"message": {"author": {"role": "user"}, "create_time": t, "content": {"parts": [m["text"]]}}}
                mapping[f"a{j}"] = {"message": {"author": {"role": "assistant"}, "create_time": t + 5,
                                                "content": {"parts": ["(assistant reply)"]}}}
            (ddir / "chatgpt" / "conversations.json").write_text(json.dumps([{"id": "c1", "mapping": mapping}]))
            msgs = []
            for m in convs_claude:
                iso = m["ts"].tz_convert("UTC").isoformat()
                msgs += [{"sender": "human", "text": m["text"], "created_at": iso},
                         {"sender": "assistant", "text": "(assistant reply)", "created_at": iso}]
            (ddir / "claude" / "conversations.json").write_text(json.dumps([{"uuid": "k1", "chat_messages": msgs}]))
            (ddir / "tiktok" / "user_data_tiktok.json").write_text(json.dumps(
                {"Your Activity": {"Watch History": {"VideoList": [
                    {"Date": t.tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S"), "Link": "https://www.tiktok.com/"} for t in tiktok]}}}))
            (ddir / "youtube" / "watch-history.json").write_text(json.dumps(
                [{"header": "YouTube", "title": "Watched a video", "time": t.tz_convert("UTC").isoformat()} for t in yt]))
            (ddir / "instagram" / "digital_days_donation_instagram.json").write_text(json.dumps(
                {"schema": "digital-days-donation/1", "source": "instagram", "records": [
                    {"ts": t.tz_convert("UTC").isoformat(), "kind": "view"} for t in insta]}))

    pd.DataFrame(devices).to_csv(out / "aware" / "aware_device.csv", index=False)
    pd.DataFrame(esms).to_csv(out / "aware" / "esms.csv", index=False)
    pd.DataFrame(screen).to_csv(out / "aware" / "screen.csv", index=False)
    pd.DataFrame(apps).to_csv(out / "aware" / "applications_foreground.csv", index=False)
    pd.DataFrame(ios_rows).to_csv(out / "ios" / "screentime_uploads.csv", index=False)
    return out

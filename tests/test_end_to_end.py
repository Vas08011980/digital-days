"""End-to-end test: simulated raw exports -> processed data -> planted effect recovered."""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from digital_days import build_aware_config, build_site, export_forms, fitbit_client, ingest, rotation, run, simulate
from digital_days.common import all_items


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    build_aware_config.main()
    export_forms.main()
    root = tmp_path_factory.mktemp("dd")
    raw = simulate.run(root / "raw", n=40)
    res = run.pipeline(raw, root / "processed")
    return root / "processed", res, raw


def test_aware_config_is_importable_shape():
    cfg = build_aware_config.build()
    titles = [q["esm_title"] for q in cfg["questions"]]
    assert len(titles) == len(set(titles)), "AWARE schedules link questions by title: titles must be unique"
    assert {s["type"] for s in cfg["schedules"]} == {"random", "interval"}
    on = {s["setting"] for s in cfg["sensors"] if s["value"] is True}
    assert "status_applications" in on and "status_screen" in on and "status_esm" in on
    for off in ("status_keyboard", "status_screentext", "status_location_gps", "status_messages", "status_calls"):
        assert off not in on, f"{off} must stay off (privacy-minimal design)"
    assert cfg["database"]["database_password"] == "-"


def test_equivalence_forms_have_three_items():
    items = all_items()
    for inst in ("MOOD_M", "LONE_M", "STRESS_M", "LS_D", "SE_D", "PRES_D", "FLOW_D", "SREP_D"):
        assert (items.instrument == inst).sum() >= 3, f"{inst}: invariance testing across bursts needs >= 3 items"


def test_prompt_counts(processed):
    out, _, _ = processed
    m = pd.read_csv(out / "moments.csv")
    assert m.groupby(["pid", "study_day"]).size().max() == 3


def test_compliance_close_to_design(processed):
    _, res, _ = processed
    assert 0.45 < res["mean_compliance"] < 0.75


def test_planted_effect_recovered(processed):
    out, _, _ = processed
    rc = run.recovery_check(pd.read_csv(out / "days.csv"))
    assert rc["beta_within"] < 0 and rc["p"] < 0.01


def test_mplus_files(processed):
    out, _, _ = processed
    assert (out / "mplus_days.dat").exists() and "NAMES" in (out / "mplus_days_names.txt").read_text()


def test_fitbit_rotation_fits():
    _, s = rotation.schedule()
    assert s["fits_in_gap"] and not s["device_overlap"]


# ---------------------------------------------------------------- Google Health API v4
def test_google_health_files_ingested(processed):
    _, _, raw = processed
    fit = ingest.fitbit_daily(raw / "fitbit")
    assert set(fit["wearable_api"]) == {"google_health_v4"}
    assert fit["sleep_min"].between(200, 700).mean() > 0.95
    assert fit["sleep_eff"].dropna().between(50, 100).all()
    assert fit["steps"].notna().mean() > 0.95 and fit["wear_valid"].mean() > 0.7


def test_google_health_client_requests(monkeypatch, tmp_path):
    """The client sends v4-shaped requests and pages through results (no network)."""
    calls = []

    class FakeResp:
        def __init__(self, js): self.js, self.status_code = js, 200
        def raise_for_status(self): pass
        def json(self): return self.js

    class FakeSession:
        def request(self, method, url, **kw):
            calls.append((method, url, kw))
            if url.endswith("sleep/dataPoints"):
                tok = kw["params"].get("pageToken")
                return FakeResp({"dataPoints": [{"sleep": {}}], **({} if tok else {"nextPageToken": "p2"})})
            if url.endswith(":dailyRollUp"):
                return FakeResp({"rollupDataPoints": [{"steps": {"countSum": "10"}}]})
            return FakeResp({"rollupDataPoints": [{"heartRate": {"beatsPerMinuteAvg": 60}}] * 3 + [{}]})

    api = fitbit_client.GoogleHealthAPI(session=FakeSession())
    s = api.sleep("t", date(2028, 1, 10), date(2028, 1, 11))
    assert len(s["dataPoints"]) == 2 and 'sleep.interval.civil_end_time < "2028-01-12"' in calls[0][2]["params"]["filter"]
    st = api.steps("t", date(2028, 1, 10), date(2028, 1, 11))
    body = calls[-1][2]["json"]
    assert body["range"]["end"]["date"] == {"year": 2028, "month": 1, "day": 12} and st["rollupDataPoints"]
    w = api.wear_minutes("t", date(2028, 1, 10), date(2028, 1, 10))
    assert w == {"2028-01-10": 3} and calls[-1][2]["json"]["windowSize"] == "60s"
    assert all(c[1].startswith("https://health.googleapis.com/v4/users/me/dataTypes/") for c in calls)


def test_legacy_fitbit_still_readable(tmp_path):
    raw = simulate.run(tmp_path / "raw", n=4, n_bursts=1, fitbit_api="legacy")
    fit = ingest.fitbit_daily(raw / "fitbit")
    assert set(fit["wearable_api"]) == {"fitbit_legacy"} and fit["steps"].notna().all()


# ---------------------------------------------------------------- donation portal + site
def test_portal_donations_ingested(processed):
    _, _, raw = processed
    ai, media = ingest.donations(raw / "donations")
    assert "instagram" in set(media["source"]) and ai.loc[ai.role == "ai", "text"].isna().all()


def test_site_data_has_all_links():
    d = build_site.build()
    urls = [l["url"] for grp in d["links"].values() for l in grp]
    assert any("health.googleapis" in u or "developers.google.com/health" in u for u in urls)
    assert any("configurator.awareframework.com" in u for u in urls)
    for p, v in d["donation_platforms"].items():
        assert v["request_url"].startswith("https://"), p

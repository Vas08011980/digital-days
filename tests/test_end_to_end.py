"""End-to-end test: simulated raw exports -> processed data -> planted effect recovered."""
import json
from pathlib import Path

import pandas as pd
import pytest

from digital_days import build_aware_config, export_forms, rotation, run, simulate
from digital_days.common import all_items


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    build_aware_config.main()
    export_forms.main()
    root = tmp_path_factory.mktemp("dd")
    raw = simulate.run(root / "raw", n=40)
    res = run.pipeline(raw, root / "processed")
    return root / "processed", res


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
        assert (items.instrument == inst).sum() >= 3, f"{inst}: WP3 invariance testing needs >= 3 items"


def test_prompt_counts(processed):
    out, _ = processed
    m = pd.read_csv(out / "moments.csv")
    assert m.groupby(["pid", "study_day"]).size().max() == 3


def test_compliance_close_to_design(processed):
    _, res = processed
    assert 0.45 < res["mean_compliance"] < 0.75


def test_planted_effect_recovered(processed):
    out, _ = processed
    rc = run.recovery_check(pd.read_csv(out / "days.csv"))
    assert rc["beta_within"] < 0 and rc["p"] < 0.01


def test_mplus_files(processed):
    out, _ = processed
    assert (out / "mplus_days.dat").exists() and "NAMES" in (out / "mplus_days_names.txt").read_text()


def test_fitbit_rotation_fits():
    _, s = rotation.schedule()
    assert s["fits_in_gap"] and not s["device_overlap"]

"""Build docs/data/pipeline.json - the single data file behind the pipeline explorer
(docs/index.html) and the donation portal (docs/donate.html).

  python -m digital_days.build_site

Everything on the two pages comes from config/*.yaml, so the website cannot drift from the code.
The pages are static: they work on GitHub Pages and inside a Zenodo-archived snapshot opened locally.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import __version__
from .common import ROOT, all_items, item_bank, load_yaml, study

STATUS = [
    ("AWARE-Light configuration", "built", "JSON in Configurator import format; DB host from RMIT ITS still needed"),
    ("EMA item bank", "drafted", "Momentary items new/adapted; cognitive pretesting in the pilot burst"),
    ("Fitbit via Google Health API v4", "built",
     "Client written against the v4 discovery document; needs RMIT Google Cloud project, OAuth client and scope verification"),
    ("Legacy Fitbit Web API", "retired", "Switched off by Google on 2 Nov 2026; reader kept for earlier backfills only"),
    ("Donation via AIO", "pending ethics", "Project configured on the AIO platform"),
    ("Donation portal (outside AIO)", "prototype",
     "Runs in the participant's browser; upload destination to be configured after ethics"),
    ("Instagram raw-DDP parser", "open", "Portal route works; raw parser waits on AIO processed format"),
    ("Problematic AI use scale", "open decision", "No instrument chosen yet"),
    ("Ethics", "not submitted", "RMIT, Northern Health, CatholicCare"),
]

PIPELINE = [
    {"id": "collect", "label": "Collect", "items": ["AWARE-Light (Android)", "EMA app (all phones)", "iOS Screen Time upload",
                                                    "Fitbit -> Google Health API", "Donations: AIO or portal"]},
    {"id": "ingest", "label": "Ingest (ingest.py)", "items": ["ESM -> prompt instances", "screen sessions + app episodes",
                                                              "Google Health v4 sleep/steps/wear", "DDP parsers (participant text only)"]},
    {"id": "align", "label": "Align (align.py)", "items": ["study day 04:00-03:59", "moments within days within bursts within persons",
                                                           "lag-1 within-person predictors", "donor zero vs missing"]},
    {"id": "qc", "label": "QC (qc.py)", "items": ["compliance", "straight-lining", "Fitbit wear >= 10 h", "passive coverage", "donation receipt"]},
    {"id": "export", "label": "Analyse", "items": ["Mplus DSEM .dat files (daily within-person models)", "secure AI-text table (language indicators)", "burst-to-burst change"]},
]


def build(recovery: dict | None = None) -> dict:
    s = study()
    bank = item_bank()
    items = all_items(bank)
    don = load_yaml("donation_instructions.yaml")
    links = load_yaml("links.yaml")
    forms = {f: int(items.instrument.isin(bank["forms"][f]).sum()) for f in ("checkin", "evening", "burst_start")}
    rec_path = ROOT / "data" / "simulated" / "processed" / "recovery_check.json"
    if recovery is None and rec_path.exists():
        recovery = json.loads(rec_path.read_text())
    return {
        "version": __version__,
        "built": str(date.today()),
        "study": s["study"], "sample": s["sample"], "bursts": s["bursts"], "ema": s["ema"],
        "passive": s["passive"], "donation": s["donation"],
        "item_counts": forms,
        "ema_items": items[items.instrument.isin(bank["forms"]["checkin"] + bank["forms"]["evening"])]
        [["instrument", "item_id", "text", "status"]].fillna("").to_dict("records"),
        "pipeline": PIPELINE,
        "status": [{"item": a, "state": b, "note": c} for a, b, c in STATUS],
        "links": links,
        "donation_platforms": don["platforms"],
        "consent_screen": don["consent_screen"],
        "direct_portal": don["direct_portal"],
        "recovery": recovery,
    }


def main(recovery: dict | None = None, out: Path | None = None) -> Path:
    out = out or ROOT / "docs" / "data" / "pipeline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(recovery), indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    # same data as a script, so the pages also work when opened straight from disk (file://)
    (out.parent / "pipeline.js").write_text("window.DD_PIPELINE = " + out.read_text(encoding="utf-8") + ";\n",
                                            encoding="utf-8")
    print(f"Site data -> {out}")
    return out


if __name__ == "__main__":
    main()

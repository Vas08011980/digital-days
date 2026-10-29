"""Compile the AWARE-Light study configuration (Android) from study.yaml + item_bank.yaml.

Output is the JSON format exported/imported by the AWARE Configurator
(https://configurator.awareframework.com -> "Update an existing study" -> upload),
reverse-engineered from github.com/awareframework/AWARE-Configurator (reactapp/src/pages/Overview.jsx).

Design decisions (privacy-minimal sensing):
  ON : foreground applications, screen on/off, notification metadata (masked text),
       battery (for data-loss diagnostics), timezone, ESM, webservice upload
  OFF: keyboard, screen text, touch text, calls/messages, location, motion sensors,
       screenshots, microphone - none is needed to answer Aims 2-3.

Limitations to state in ethics:
  * AWARE scheduling uses whole hours; the evening report fires at 20:00 (not 20:30).
  * ESM schedules run while the participant is joined to the study; bursts are
    implemented by joining at burst start and leaving at burst end (coordinator
    checklist), or by using a separate EMA app for all participants.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, all_items, item_bank, study

ESM_TYPE = {"free_text": 1, "radio": 2, "checkbox": 3, "likert": 4, "quick": 5, "scale": 6, "numeric": 7}

ALL_SENSOR_SETTINGS = (
    "webservice_wifi_only webservice_charging frequency_webservice frequency_clean_old_data webservice_silent "
    "fallback_network remind_to_charge foreground_priority debug_flag frequency_sync_config enable_config_update "
    "status_applications status_notifications status_crashes frequency_applications status_keyboard mask_notification "
    "mask_keyboard mask_touch_text status_screentext package_specification package_names status_battery communication "
    "status_communication_events status_calls status_messages status_installations status_screen status_touch "
    "status_telephony status_timezone frequency_timezone status_accelerometer frequency_accelerometer "
    "threshold_accelerometer frequency_accelerometer_enforce status_barometer frequency_barometer threshold_barometer "
    "frequency_barometer_enforce status_bluetooth frequency_bluetooth status_gravity frequency_gravity threshold_gravity "
    "frequency_gravity_enforce status_gyroscope frequency_gyroscope threshold_gyroscope frequency_gyroscope_enforce "
    "status_light frequency_light threshold_light frequency_light_enforce status_linear_accelerometer "
    "frequency_linear_accelerometer threshold_linear_accelerometer frequency_linear_accelerometer_enforce location "
    "status_location_gps status_location_network frequency_gps frequency_network min_location_gps_accuracy "
    "min_location_network_accuracy location_expiration_time status_location_passive location_save_all "
    "status_magnetometer frequency_magnetometer threshold_magnetometer frequency_magnetometer_enforce network "
    "status_network_events status_network_traffic status_processor status_proximity frequency_proximity "
    "threshold_proximity frequency_proximity_enforce status_rotation frequency_rotation threshold_rotation "
    "frequency_rotation_enforce status_temperature frequency_temperature threshold_temperature "
    "frequency_temperature_enforce status_wifi frequency_wifi status_screenshot capture_time_interval compress_rate "
    "status_screenshot_local_storage screenshot_package_names screenshot_package_specification status_notes "
    "status_plugin_ambient_noise frequency_plugin_ambient_noise plugin_ambient_noise_sample_size "
    "plugin_ambient_noise_silence_threshold status_plugin_openweather plugin_openweather_frequency "
    "plugin_openweather_api_key plugin_openweather_measurement_units status_esm status_webservice"
).split()

SENSOR_ON = {
    # upload behaviour
    "status_webservice": True,
    "webservice_wifi_only": True,
    "fallback_network": 6,            # hours before falling back to mobile data
    "frequency_webservice": 30,       # minutes
    "frequency_clean_old_data": 1,    # weekly local clean-up after successful sync
    "webservice_silent": True,
    "remind_to_charge": True,
    "foreground_priority": True,
    "frequency_sync_config": 60,
    "enable_config_update": True,
    # what we measure
    "status_applications": True,      # foreground app -> per-app screen time (needs Accessibility permission)
    "frequency_applications": 30,
    "status_notifications": True,
    "mask_notification": True,        # keep package + timestamp, drop notification text
    "mask_keyboard": True,
    "mask_touch_text": True,
    "status_screen": True,
    "status_battery": True,
    "status_timezone": True,
    "frequency_timezone": 3600,
    "status_esm": True,
}


def _question(item: dict, idx: int, cfg: dict) -> dict:
    t = item["resp_type"]
    q = {
        "esm_type": ESM_TYPE[t],
        # The Configurator has no separate instructions field: esm_title is the question shown
        # to the participant, and schedules reference questions by title, so titles must be unique.
        # build/aware/esm_title_map.csv maps each title back to its item_id for ingestion.
        "esm_title": item["text"],
        "esm_submit": "Next",
        "esm_notification_timeout": cfg["expiry_minutes"] * 60,
        "esm_expiration_threshold": 0,
    }
    if t == "likert":
        q.update(esm_likert_max=int(item["resp_max"]), esm_likert_max_label=item["max_label"],
                 esm_likert_min_label=item["min_label"])
    elif t == "scale":
        q.update(esm_scale_min=int(item["resp_min"]), esm_scale_max=int(item["resp_max"]),
                 esm_scale_start=int((item["resp_min"] + item["resp_max"]) // 2), esm_scale_step=1,
                 esm_scale_min_label=item["min_label"], esm_scale_max_label=item["max_label"])
    elif t == "radio":
        q["esm_radios"] = item["options"].split("|")
    elif t == "checkbox":
        q["esm_checkboxes"] = item["options"].split("|")
    return q


def build(researcher: dict | None = None) -> dict:
    s, bank = study(), item_bank()
    items = all_items(bank).set_index("instrument")
    researcher = researcher or {"first": "Vasileios", "last": "Stavropoulos",
                                "contact": "vasileios.stavropoulos@rmit.edu.au"}
    forms = bank["forms"]
    questions, title_to_id = [], {}
    for form in ("checkin", "evening"):
        cfg = s["ema"]["checkins" if form == "checkin" else "evening"]
        for inst in forms[form]:
            for _, row in items.loc[[inst]].reset_index().iterrows():
                if row["item_id"] in title_to_id:
                    continue
                q = _question(row.to_dict(), len(questions) + 1, cfg)
                questions.append({**q, "id": len(questions) + 1})
                title_to_id[row["item_id"]] = len(questions)

    def ids(form):
        return [title_to_id[i] for inst in forms[form] for i in items.loc[[inst], "item_id"]]

    ci, ev = s["ema"]["checkins"], s["ema"]["evening"]
    schedules = [
        {"title": "Check-in", "type": "random", "esm_keep": False, "questions": ids("checkin"),
         "firsthour": int(ci["window"][0][:2]), "lasthour": int(ci["window"][1][:2]),
         "randomCount": ci["per_day"], "randomInterval": ci["min_gap_minutes"]},
        {"title": "Evening report", "type": "interval", "esm_keep": True, "questions": ids("evening"),
         "hours": [int(ev["time"][:2])],
         "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
    ]
    sensors = [{"setting": k, "value": SENSOR_ON.get(k, False)} for k in ALL_SENSOR_SETTINGS]
    now = datetime.now(timezone.utc).isoformat()
    return {
        "_id": None,
        "study_info": {
            "study_title": "Digital Days (Years and moments, WP2)",
            "study_description": (
                "Four 14-day bursts, six months apart. Three short check-ins a day and one evening report. "
                "On Android, the app also logs which apps are on screen and when the screen is on; "
                "it does not record what you type, read or say, your location or your messages."),
            "researcher_first": researcher["first"],
            "researcher_last": researcher["last"],
            "researcher_contact": researcher["contact"],
        },
        "database": {
            "database_host": "TO-BE-PROVIDED-BY-RMIT-ITS",
            "database_port": 3306,
            "database_name": "digital_days",
            "database_username": "aware_client",
            "database_password": "-",
            "config_without_password": True,   # never commit credentials; enter in the Configurator
            "require_ssl": True,
            "rootUsername": "-",
            "rootPassword": "-",
        },
        "createdAt": now,
        "updatedAt": now,
        "questions": questions,
        "schedules": schedules,
        "sensors": sensors,
    }


def main(out: str | None = None) -> Path:
    path = Path(out) if out else ROOT / "build" / "aware" / "aware_light_study_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = build()
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    items = all_items()
    title_map = items.loc[items["text"].isin([q["esm_title"] for q in cfg["questions"]]),
                          ["item_id", "text", "instrument", "reverse", "resp_min", "resp_max"]]
    title_map.rename(columns={"text": "esm_title"}).to_csv(path.parent / "esm_title_map.csv", index=False)
    n_ci = len(cfg["schedules"][0]["questions"])
    n_ev = len(cfg["schedules"][1]["questions"])
    print(f"AWARE config -> {path}  ({len(cfg['questions'])} questions; check-in {n_ci}, evening {n_ev})")
    return path


if __name__ == "__main__":
    main(*sys.argv[1:])

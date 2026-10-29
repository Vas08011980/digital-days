"""Export platform-neutral forms and the codebook.

build/forms/ema_items.csv     - every EMA item with schedule slot, for any EMA app (e.g., m-Path, SEMA3)
build/forms/full_scales.csv   - burst-start / follow-up survey items (Qualtrics/REDCap import)
build/forms/codebook.csv      - one row per variable in the analysis-ready dataset
build/forms/public_item_bank.csv - item text stripped where licence != open/own (for public release)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .common import ROOT, all_items, item_bank, study


def main() -> Path:
    out = ROOT / "build" / "forms"
    out.mkdir(parents=True, exist_ok=True)
    bank, s = item_bank(), study()
    items = all_items(bank)
    forms = bank["forms"]

    ema = []
    for form in ("checkin", "evening"):
        for order, inst in enumerate(forms[form]):
            sub = items[items.instrument == inst].copy()
            sub.insert(0, "form", form)
            sub.insert(1, "block_order", order)
            ema.append(sub)
    ema = pd.concat(ema)
    ema["schedule"] = ema["form"].map({
        "checkin": f"{s['ema']['checkins']['per_day']} random/day {s['ema']['checkins']['window'][0]}-"
                   f"{s['ema']['checkins']['window'][1]}, >= {s['ema']['checkins']['min_gap_minutes']} min apart",
        "evening": f"daily {s['ema']['evening']['time']}"})
    ema.to_csv(out / "ema_items.csv", index=False)

    full = items[items.instrument.isin(forms["burst_start"])].copy()
    full.to_csv(out / "full_scales.csv", index=False)

    pub = items.copy()
    pub.loc[~pub.licence.isin(["open", "own"]), "text"] = "[see original publication]"
    pub.to_csv(out / "public_item_bank.csv", index=False)

    cb = items[["instrument", "item_id", "status", "reverse", "resp_type", "resp_min", "resp_max"]].copy()
    cb["level"] = cb["instrument"].map(lambda c: "moment" if c.endswith("_M") else "day" if c.endswith("_D") else "burst")
    derived = pd.DataFrame([
        ("derived", "mood_m", "moment", "mean of MOOD_M1, MOOD_M2, 8-MOOD_M3"),
        ("derived", "lonely_m", "moment", "mean of LONE_M1, LONE_M2, 8-LONE_M3"),
        ("derived", "stress_m", "moment", "mean of STRESS_M1, STRESS_M2, 8-STRESS_M3"),
        ("derived", "ls_d", "day", "mean of LS_D1-3"),
        ("derived", "se_d", "day", "mean of SE_D1, SE_D2, 8-SE_D3"),
        ("derived", "presence_d", "day", "mean of PRES_D1-3"),
        ("derived", "flow_d", "day", "mean of FLOW_D1-3"),
        ("derived", "selfrep_d", "day", "mean of SREP_D1-3"),
        ("passive", "screen_min_prev60", "moment", "Android: minutes screen unlocked in the 60 min before the prompt"),
        ("passive", "ai_min_prev60", "moment", "Android: minutes AI chatbot/companion apps in foreground, 60 min before prompt"),
        ("passive", "screen_min_day", "day", "Android: unlocked minutes per study day; iOS: weekly Screen Time / 7"),
        ("passive", "night_use_min", "day", "Android: unlocked minutes 00:00-05:00 the night before"),
        ("passive", "sm_min_day", "day", "Android: minutes in social media + short video apps"),
        ("passive", "ai_app_min_day", "day", "Android: minutes in AI chatbot/companion apps"),
        ("wearable", "sleep_min", "day", "Fitbit: minutes asleep, main sleep ending on this study day"),
        ("wearable", "sleep_eff", "day", "Fitbit: sleep efficiency"),
        ("wearable", "steps", "day", "Fitbit: steps"),
        ("wearable", "wear_valid", "day", "Fitbit: >= 10 h of data"),
        ("donation", "ai_msgs_day", "day", "Donated ChatGPT/Claude: participant messages sent"),
        ("donation", "ai_words_day", "day", "Donated ChatGPT/Claude: participant words"),
        ("donation", "ai_late_msgs", "day", "Donated ChatGPT/Claude: participant messages 23:00-04:00"),
        ("donation", "tiktok_views_day", "day", "Donated TikTok: videos watched"),
        ("donation", "youtube_views_day", "day", "Donated YouTube: videos watched"),
    ], columns=["instrument", "item_id", "level", "definition"])
    pd.concat([cb, derived]).to_csv(out / "codebook.csv", index=False)
    print(f"Forms -> {out}  (EMA items: {len(ema)}; full-scale items: {len(full)})")
    return out


if __name__ == "__main__":
    main()

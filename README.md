# Digital Days: fast-clock pipeline for *Years and moments*

This is the data pipeline for the WP2–WP3 measurement-burst study. About 400 young adults aged 18–25 take part in four 14-day bursts, six months apart, followed by a final follow-up survey. Five data streams are lined up on one timeline:

| Stream | Tool | Captured | Fallback |
|---|---|---|---|
| Full scales | Qualtrics/REDCap | SWLS, RSES, DASS-21, BSMAS, IGDS9-SF, presence, flow, self-representation, at burst start and follow-up | – |
| EMA | AWARE-Light ESM, or one cross-platform EMA app | 3 check-ins a day (mood, loneliness, stress, online use) + evening report (sleep, mood, life satisfaction, self-esteem, AI use and purpose, presence, flow, self-representation) | – |
| Phone use | AWARE-Light 4.2.1 (Android) | foreground apps, screen unlock sessions, notification metadata | iOS: weekly Screen Time upload |
| Wearable | Fitbit, via the Google Health API | sleep, steps, wear time | Fitabase export |
| Donation | Australian Internet Observatory (AIO) | TikTok, YouTube, Instagram, ChatGPT, Claude data download packages (DDPs) | – |

![Fast-clock design](docs/fig_fast_clock_design.png)

## Run it

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m digital_days.run --simulate      # configs + simulated raw data + full pipeline + recovery check
PYTHONPATH=src python -m pytest -q tests                  # 7 end-to-end tests
PYTHONPATH=src python -m digital_days.figure              # regenerate the C1 figure
```

`--simulate` writes every raw stream in its native format: AWARE MySQL table exports, Fitbit JSON, ChatGPT/Claude `conversations.json`, TikTok and Takeout JSON, and iOS uploads. It then runs ingest, alignment, quality control (QC) and the Mplus export, and checks that a planted within-person effect is recovered. That effect is late-night AI chatting lowering next-day mood. On the reference run (N = 60, 2 bursts) the recovered effect was β = −0.38 (planted −0.30, p < .001), with 59% compliance and no Fitbit device clashes across 400 participants.

## What is where

```
config/study.yaml                 design parameters (single source of truth)
config/item_bank.yaml             full scales + EMA forms, status (published/adapted/new), licence, parent item
config/demographics.yaml          baseline demographics incl. quota variables and OS routing
config/donation_instructions.yaml participant-facing DDP request steps + links (for the AIO uploader)
config/app_categories.csv         Android package -> category (AI chatbot, social media, ...)
src/digital_days/
  build_aware_config.py   -> build/aware/aware_light_study_config.json (import in the AWARE Configurator)
  export_forms.py         -> build/forms/ (EMA items, full scales, codebook, public item bank)
  fitbit_client.py        Fitbit Web API (legacy) / Google Health API (stub) / Fitabase
  ingest.py               AWARE, iOS, Fitbit and donation parsers
  align.py                moments within days within bursts within persons; lagged within-person predictors
  qc.py                   compliance, straight-lining, wear time, passive coverage, donation receipt
  rotation.py             Fitbit device rotation scheduler
  simulate.py             native-format synthetic data with a planted effect
  run.py                  orchestration + Mplus .dat export + recovery check
  figure.py               C1 figure
```

## Status: what is done and what still needs a person

| Item | Status |
|---|---|
| AWARE-Light study configuration (JSON in Configurator import format) | **Built.** Import via configurator.awareframework.com → "Update an existing study". Database host still needed (RMIT ITS). |
| EMA item bank (35 items; every construct tested for invariance across bursts has ≥3 items) | **Drafted.** Momentary items are *new or adapted*; cognitive pretesting in the pilot burst |
| Full-scale battery | **Mapped.** Text for SPQ, UAB, Proteus and IGDS9-SF lives in the 2021 battery; add once licences are checked |
| Problematic AI use scale | **Open decision.** No instrument chosen yet |
| Fitbit | **Legacy client written.** Google Health API adapter left unimplemented on purpose: Google's documentation needs checking first, and minute-level (intraday) access is not confirmed |
| AIO donation | Parsers written for ChatGPT, Claude, TikTok and YouTube. Instagram parser waits on the AIO's processed output format. DDP instructions in `config/donation_instructions.yaml` |
| iOS | Weekly Screen Time schema; screenshot transcription is manual (double entry) |
| Ethics | Not yet submitted for this design |

## Design decisions worth defending

- **Privacy-minimal sensing.** Keyboard, screen text, messages, calls, location and motion sensors are all off. Nothing needed for Aims 2–3 depends on them.
- **Study day runs 04:00–03:59.** Late-night use belongs to the evening before, so its effect is tested on the *next* day.
- **AI-chat text.** The pipeline keeps only participants' own words, which feed the WP2.2 language indicators. The AI's replies are reduced to timestamp and length. Text stays in secure RMIT storage and is never exported (`*_SECURE.csv` is gitignored).
- **Missing versus zero.** For people who donated, a day with no donated activity counts as zero. For people who did not donate, it is missing.
- **One EMA delivery app for everyone, if possible.** Otherwise the phone's operating system is confounded with how prompts are delivered, and the OS likely tracks socioeconomic background, one of the quota variables.

## Suggested C1 wording

> The WP2 protocol is specified as executable code (sensor configuration, prompt schedule, item bank, ingestion and alignment scripts), tested end to end on simulated data that recover a planted within-person effect, and openly versioned³⁹ (Fig. X).

Do **not** write "pilot pipeline tested" until the 30-person pilot burst has run.

## Data governance

No participant data goes in this repository. `data/` and `build/` are gitignored except for documentation. The participant-code salt, database credentials and API tokens live outside the repository.

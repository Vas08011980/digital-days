"""C1 figure: the fast-clock design on one timeline (greyscale-safe, ~half A4 width).

python -m digital_days.figure  -> docs/fig_fast_clock_design.{png,svg}
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

from .common import ROOT

INK, MID, LIGHT, PALE = "#1a1a1a", "#5f5f5f", "#a8a8a8", "#e3e3e3"


def draw(path_stem=None):
    path_stem = path_stem or ROOT / "docs" / "fig_fast_clock_design"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7.5})
    fig, ax = plt.subplots(figsize=(7.1, 2.9), dpi=300)
    bursts = [13, 19, 25, 31]          # project month each burst round opens (staggered over ~2.5 months)
    stagger = 2.4
    lanes = ["Full scales", "EMA prompts", "Phone use", "Fitbit", "Donation (AIO)"]
    y = {name: len(lanes) - i for i, name in enumerate(lanes)}
    h = 0.62

    for name, yy in y.items():
        ax.text(11.6, yy, name, ha="right", va="center", color=INK, fontweight="bold")
        ax.plot([12, 44], [yy, yy], color=PALE, lw=0.6, zorder=0)

    for k, m in enumerate(bursts):
        # burst round band
        ax.add_patch(Rectangle((m, 0.45), stagger, len(lanes) + 0.1, color=PALE, alpha=0.55, lw=0, zorder=0))
        ax.text(m + stagger / 2, len(lanes) + 0.75, f"Burst {k+1}", ha="center", color=INK, fontweight="bold")
        # full scales at burst start
        ax.plot(m + 0.15, y["Full scales"], marker="D", ms=4.5, color=INK)
        # EMA: 14 days, 3 check-ins + 1 evening
        ax.add_patch(Rectangle((m, y["EMA prompts"] - h / 2), stagger, h, color=INK, lw=0))
        # phone use: Android sensing (solid) / iOS weekly upload (hatched)
        ax.add_patch(Rectangle((m, y["Phone use"]), stagger, h / 2, color=MID, lw=0))
        ax.add_patch(Rectangle((m, y["Phone use"] - h / 2), stagger, h / 2, facecolor="white", edgecolor=MID,
                               hatch="////", lw=0.6))
        # Fitbit
        ax.add_patch(Rectangle((m, y["Fitbit"] - h / 2), stagger, h, color=LIGHT, lw=0))
        # donation at burst end, reaching back over the preceding gap
        end = m + stagger
        ax.plot(end, y["Donation (AIO)"], marker="v", ms=5, color=INK)
        back = bursts[k - 1] + stagger if k else m - 2.5
        arr = FancyArrowPatch((end, y["Donation (AIO)"] - 0.18), (back, y["Donation (AIO)"] - 0.18),
                              arrowstyle="-|>", mutation_scale=6, color=MID, lw=0.7, linestyle=(0, (2, 1.5)))
        ax.add_patch(arr)

    ax.plot(43, y["Full scales"], marker="D", ms=4.5, color=INK)
    ax.text(43, y["Full scales"] + 0.42, "Follow-up", ha="center", color=MID, fontsize=6.5)

    # in-figure legend (right-hand notes)
    notes = {
        "EMA prompts": "3 × ~1-min check-ins + ~3-min evening report, 14 days",
        "Phone use": "Android: AWARE-Light logging  |  iOS: weekly Screen Time",
        "Fitbit": "sleep, steps; 100 devices rotate across 4 staggered cohorts",
        "Donation (AIO)": "TikTok, YouTube, Instagram, ChatGPT, Claude: back-fills gaps",
        "Full scales": "life satisfaction, self-esteem, distress, problematic use, presence/flow/self-rep.",
    }
    for lane, txt in notes.items():
        ax.text(44.6, y[lane], txt, va="center", color=MID, fontsize=6.3)

    ax.set_xlim(4, 66)
    ax.set_ylim(0.1, len(lanes) + 1.2)
    ax.set_xticks([13, 19, 25, 31, 37, 43])
    ax.set_xticklabels([f"M{m}" for m in [13, 19, 25, 31, 37, 43]], color=MID)
    ax.tick_params(axis="x", length=2, color=LIGHT)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_bounds(12, 44)
    ax.spines["bottom"].set_color(LIGHT)
    ax.set_yticks([])
    ax.text(12, -0.55, "Project month.  Analysis structure: prompts within days within bursts within persons "
                       "(WP2: burst 1; WP3: bursts 1–4 → follow-up).", color=INK, fontsize=6.6, transform=ax.transData)
    fig.tight_layout()
    path_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(f"{path_stem}.png", bbox_inches="tight")
    fig.savefig(f"{path_stem}.svg", bbox_inches="tight")
    plt.close(fig)
    return f"{path_stem}.png"


if __name__ == "__main__":
    print(draw())

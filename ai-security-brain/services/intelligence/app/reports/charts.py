"""Matplotlib chart rendering for PDF reports. Returns PNG file paths."""

import os
import tempfile
import logging
from collections import Counter
from datetime import datetime

logger = logging.getLogger("intelligence.reports.charts")

# Use non-interactive backend
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# Dark theme matching the dashboard
COLORS = {
    "bg": "#0B0F19",
    "surface": "#111827",
    "text": "#E8ECF1",
    "muted": "#64748B",
    "border": "#1E293B",
    "teal": "#00C9A7",
    "blue": "#3B82F6",
    "amber": "#F59E0B",
    "red": "#EF4444",
    "purple": "#8B5CF6",
    "green": "#10B981",
}

TYPE_COLORS = {
    "PROXIMITY_ALERT": COLORS["red"],
    "SPEED_VIOLATION": COLORS["amber"],
    "ESTOP_TRIGGERED": COLORS["purple"],
    "PATH_DEVIATION": COLORS["blue"],
    "SENSOR_FAILURE": COLORS["muted"],
    "ROBOT_FAULT": COLORS["amber"],
    "ZONE_BREACH": COLORS["red"],
}


def _setup_dark_axes(ax: plt.Axes):
    ax.set_facecolor(COLORS["surface"])
    ax.tick_params(colors=COLORS["muted"], labelsize=7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(COLORS["border"])
    ax.spines["bottom"].set_color(COLORS["border"])
    ax.xaxis.label.set_color(COLORS["muted"])
    ax.yaxis.label.set_color(COLORS["muted"])
    ax.title.set_color(COLORS["text"])


def render_incident_scatter(incidents: list[dict], hotspots: list[dict]) -> str:
    """Scatter plot of incident positions with hotspot circles."""
    fig, ax = plt.subplots(figsize=(6, 4))
    fig.patch.set_facecolor(COLORS["bg"])
    _setup_dark_axes(ax)

    # Plot incidents
    for inc in incidents:
        x, y = inc.get("position_x", 0), inc.get("position_y", 0)
        if x == 0 and y == 0:
            continue
        etype = inc.get("event_type", "UNKNOWN")
        color = TYPE_COLORS.get(etype, COLORS["muted"])
        ax.scatter(x, y, c=color, s=8, alpha=0.6, edgecolors="none")

    # Plot hotspot circles
    for hs in hotspots:
        circle = plt.Circle(
            (hs["center_x"], hs["center_y"]),
            hs["radius_m"],
            fill=False,
            edgecolor=COLORS["red"],
            linewidth=1.5,
            linestyle="--",
            alpha=0.7,
        )
        ax.add_patch(circle)
        ax.annotate(
            f'{hs.get("incident_count", 0)}',
            (hs["center_x"], hs["center_y"]),
            color=COLORS["red"],
            fontsize=7,
            ha="center",
            va="center",
        )

    ax.set_title("Incident Positions & Hotspots", fontsize=9, pad=8)
    ax.set_xlabel("X (meters)", fontsize=7)
    ax.set_ylabel("Y (meters)", fontsize=7)
    ax.set_aspect("equal")

    path = os.path.join(tempfile.gettempdir(), f"scatter_{datetime.now().strftime('%Y%m%d%H%M%S')}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def render_hourly_histogram(incidents: list[dict], patterns: list[dict]) -> str:
    """Bar chart of incidents by hour of day with shift-change annotations."""
    hour_counts = [0] * 24
    for inc in incidents:
        ts = inc.get("occurred_at")
        if isinstance(ts, datetime):
            hour_counts[ts.hour] += 1

    fig, ax = plt.subplots(figsize=(6, 2.5))
    fig.patch.set_facecolor(COLORS["bg"])
    _setup_dark_axes(ax)

    bars = ax.bar(range(24), hour_counts, color=COLORS["teal"], alpha=0.8, width=0.7)

    # Annotate shift changes
    for pat in patterns:
        if pat.get("pattern_type") == "shift_change":
            h = pat.get("hour_of_day", 0)
            ax.axvline(x=h, color=COLORS["amber"], linestyle="--", linewidth=1, alpha=0.7)
            ax.text(h + 0.3, max(hour_counts) * 0.9, "shift", color=COLORS["amber"], fontsize=6, rotation=90, va="top")

    ax.set_title("Incidents by Hour of Day", fontsize=9, pad=8)
    ax.set_xlabel("Hour", fontsize=7)
    ax.set_ylabel("Count", fontsize=7)
    ax.set_xticks(range(0, 24, 3))
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    path = os.path.join(tempfile.gettempdir(), f"hourly_{datetime.now().strftime('%Y%m%d%H%M%S')}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def render_root_cause_pie(incidents: list[dict]) -> str:
    """Pie chart of root cause breakdown."""
    causes = Counter(i.get("root_cause") or "unknown" for i in incidents if not i.get("is_hallucination"))
    if not causes:
        causes = Counter({"no_data": 1})

    labels = [c.replace("_", " ").title() for c in causes.keys()]
    sizes = list(causes.values())
    colors = [COLORS["teal"], COLORS["blue"], COLORS["amber"], COLORS["red"],
              COLORS["purple"], COLORS["green"], COLORS["muted"]][:len(labels)]

    fig, ax = plt.subplots(figsize=(4, 3))
    fig.patch.set_facecolor(COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors, autopct="%1.0f%%",
        textprops={"color": COLORS["text"], "fontsize": 7},
        pctdistance=0.8, startangle=90,
    )
    for t in autotexts:
        t.set_fontsize(6)
        t.set_color(COLORS["text"])
    ax.set_title("Root Cause Breakdown", fontsize=9, color=COLORS["text"], pad=8)

    path = os.path.join(tempfile.gettempdir(), f"pie_{datetime.now().strftime('%Y%m%d%H%M%S')}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def render_battery_trend(robot_id: str, daily_avgs: list[tuple]) -> str:
    """Line chart of daily battery voltage trend."""
    if len(daily_avgs) < 2:
        return ""
    days = [d[0] for d in daily_avgs]
    voltages = [d[1] for d in daily_avgs]

    fig, ax = plt.subplots(figsize=(5, 2))
    fig.patch.set_facecolor(COLORS["bg"])
    _setup_dark_axes(ax)

    ax.plot(days, voltages, color=COLORS["amber"], linewidth=1.5)
    ax.fill_between(days, voltages, alpha=0.1, color=COLORS["amber"])
    ax.set_title(f"Battery Trend: {robot_id}", fontsize=8, pad=6)
    ax.set_ylabel("Voltage", fontsize=7)
    ax.tick_params(axis="x", rotation=45)

    path = os.path.join(tempfile.gettempdir(), f"battery_{robot_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path

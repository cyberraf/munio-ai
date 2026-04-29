"""Temporal pattern detection — hourly spikes, shift-change, day-of-week patterns.

Analyzes incident timing over the last 30 days to find recurring temporal patterns.
Generates actionable recommendations for each detected pattern.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.db import postgres

logger = logging.getLogger("intelligence.analysis.temporal")

DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SHIFT_TIMES = [6, 14, 22]  # Common 3-shift pattern: 06:00, 14:00, 22:00


async def detect_patterns(facility_id: str, days: int = 30):
    """Analyze incident timing to find recurring temporal patterns.

    Detects:
      1. Hourly spikes — hours with >2x the baseline incident rate.
      2. Shift-change patterns — ±1 hour around common shift times with >1.5x rate.
      3. Day-of-week patterns — days with >1.5x the average daily rate.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    incidents = await postgres.get_incidents(facility_id=facility_id, since=since)

    total = len(incidents)
    if total < 10:
        logger.info(f"[{facility_id}] only {total} incidents — skipping temporal analysis")
        return []

    # Build histograms
    hour_counts: dict[int, int] = defaultdict(int)
    dow_counts: dict[int, int] = defaultdict(int)
    for inc in incidents:
        ts = inc.get("occurred_at")
        if isinstance(ts, datetime):
            hour_counts[ts.hour] += 1
            dow_counts[ts.weekday()] += 1

    baseline_hourly = total / (24 * days)
    baseline_daily = total / max(days, 1)

    # Count weeks in the period for accurate day-of-week rate
    weeks = max(1, days // 7)

    patterns = []
    shift_hours_flagged: set[int] = set()

    # ─── 1. Shift-change detection (check first, so we can skip those hours later) ──
    for shift_hour in SHIFT_TIMES:
        window_hours = [shift_hour - 1, shift_hour, shift_hour + 1]
        window_hours = [h % 24 for h in window_hours]
        window_count = sum(hour_counts.get(h, 0) for h in window_hours)
        window_rate = window_count / (3 * days)  # per-hour rate across the 3-hour window

        if baseline_hourly > 0 and window_rate > baseline_hourly * 1.5:
            multiplier = window_rate / baseline_hourly
            shift_hours_flagged.update(window_hours)
            patterns.append({
                "pattern_type": "shift_change",
                "description": (
                    f"Shift change at {shift_hour:02d}:00 — "
                    f"{window_count} incidents in the ±1hr window over {days} days "
                    f"({multiplier:.1f}x baseline)"
                ),
                "hour_of_day": shift_hour,
                "incident_rate": round(window_rate, 3),
                "baseline_rate": round(baseline_hourly, 3),
                "multiplier": round(multiplier, 1),
                "recommendation": (
                    f"Consider pausing robot operations for 15 minutes during the "
                    f"{shift_hour:02d}:00 shift transition, or stagger shift start times "
                    f"to reduce human-robot congestion."
                ),
            })

    # ─── 2. Hourly spikes (excluding hours already flagged as shift-change) ──────
    for hour in range(24):
        if hour in shift_hours_flagged:
            continue
        count = hour_counts.get(hour, 0)
        rate = count / max(days, 1)
        if baseline_hourly > 0 and rate > baseline_hourly * 2:
            multiplier = rate / baseline_hourly
            patterns.append({
                "pattern_type": "hourly_spike",
                "description": (
                    f"Incident spike at {hour:02d}:00 — "
                    f"{multiplier:.1f}x baseline ({rate:.2f}/hr vs {baseline_hourly:.2f}/hr)"
                ),
                "hour_of_day": hour,
                "incident_rate": round(rate, 3),
                "baseline_rate": round(baseline_hourly, 3),
                "multiplier": round(multiplier, 1),
                "recommendation": (
                    f"Investigate conditions at {hour:02d}:00 — "
                    f"possible delivery schedule, cleaning crew, break-room traffic, "
                    f"or environmental factor (lighting change, door opening)."
                ),
            })

    # ─── 3. Day-of-week patterns ─────────────────────────────────────────────────
    for dow in range(7):
        count = dow_counts.get(dow, 0)
        rate = count / max(weeks, 1)
        avg_daily = baseline_daily / 7 * weeks  # expected per day-of-week slot
        if avg_daily > 0 and rate > (baseline_daily * 1.5):
            multiplier = rate / baseline_daily
            patterns.append({
                "pattern_type": "day_of_week",
                "description": (
                    f"{DOW_NAMES[dow]}s have {multiplier:.1f}x more incidents than average "
                    f"({count} total in {weeks} weeks)"
                ),
                "day_of_week": dow,
                "incident_rate": round(rate, 2),
                "baseline_rate": round(baseline_daily, 2),
                "multiplier": round(multiplier, 1),
                "recommendation": (
                    f"Review {DOW_NAMES[dow]} operations — staffing levels, robot workload, "
                    f"facility conditions, or delivery schedules may differ on this day."
                ),
            })

    # Sort by multiplier descending
    patterns.sort(key=lambda p: p.get("multiplier", 0), reverse=True)

    await postgres.upsert_patterns(facility_id, patterns)
    logger.info(
        f"[{facility_id}] detected {len(patterns)} temporal patterns from {total} incidents: "
        f"{sum(1 for p in patterns if p['pattern_type'] == 'shift_change')} shift-change, "
        f"{sum(1 for p in patterns if p['pattern_type'] == 'hourly_spike')} hourly spikes, "
        f"{sum(1 for p in patterns if p['pattern_type'] == 'day_of_week')} day-of-week"
    )
    return patterns

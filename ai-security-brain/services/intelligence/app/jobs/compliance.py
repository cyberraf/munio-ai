"""Daily compliance monitoring: 24-hour uptime tracking and monthly auto-generation."""

import logging
from datetime import date, datetime, timedelta, timezone

from app.db import clickhouse, postgres
from app.reports.compliance_report import generate_compliance_report

logger = logging.getLogger("intelligence.jobs.compliance")

FRAMEWORKS = ["EU_AI_ACT", "ISO_10218", "OSHA_1910", "ANSI_R15_08"]
_UPTIME_WARN_PCT = 95.0
_GAP_THRESHOLD = timedelta(minutes=5)


async def run_compliance_jobs() -> None:
    """Daily compliance check for every facility, plus month-1 report auto-generation."""
    logger.info("=== Starting compliance batch ===")
    now = datetime.now(timezone.utc)

    try:
        facilities = await postgres.get_facilities()
    except Exception as e:
        logger.error(f"Failed to fetch facilities: {e}")
        logger.info("=== Compliance batch aborted ===")
        return

    for f in facilities:
        fid = f["id"]
        try:
            await _check_facility(fid, now)
        except Exception as e:
            logger.error(f"[{fid}] compliance check failed: {e}")

    if now.day == 1:
        await _auto_generate_monthly_reports(facilities, now)

    logger.info("=== Compliance batch complete ===")


# ─── Per-facility daily check ─────────────────────────────────────────────────

async def _check_facility(facility_id: str, now: datetime) -> None:
    """Calculate 24h uptime and per-framework incident counts; upsert to DB."""
    window_start = now - timedelta(hours=24)

    uptime_pct, monitored_hours, gap_count, max_gap_minutes = _calc_uptime(
        facility_id, window_start, now
    )

    if uptime_pct < _UPTIME_WARN_PCT:
        logger.warning(
            f"[{facility_id}] monitoring uptime {uptime_pct:.1f}% below "
            f"{_UPTIME_WARN_PCT}% — {gap_count} gap(s), largest {max_gap_minutes:.0f} min"
        )

    pool = await postgres.get_pool()
    counts = {
        fw: await _count_reportable(pool, facility_id, window_start, now, fw)
        for fw in FRAMEWORKS
    }

    if uptime_pct < 50.0:
        overall_status = "non_compliant"
    elif uptime_pct < _UPTIME_WARN_PCT or any(v > 0 for v in counts.values()):
        overall_status = "attention_needed"
    else:
        overall_status = "compliant"

    await pool.execute(
        """INSERT INTO compliance_daily_status
            (facility_id, checked_date,
             total_hours, monitored_hours, uptime_pct,
             gap_count, max_gap_minutes,
             reportable_eu_ai_act, reportable_iso_10218,
             reportable_osha, reportable_ansi_r15_08,
             overall_status)
        VALUES ($1,$2, 24.0,$3,$4, $5,$6, $7,$8,$9,$10, $11)
        ON CONFLICT (facility_id, checked_date) DO UPDATE SET
            monitored_hours        = EXCLUDED.monitored_hours,
            uptime_pct             = EXCLUDED.uptime_pct,
            gap_count              = EXCLUDED.gap_count,
            max_gap_minutes        = EXCLUDED.max_gap_minutes,
            reportable_eu_ai_act   = EXCLUDED.reportable_eu_ai_act,
            reportable_iso_10218   = EXCLUDED.reportable_iso_10218,
            reportable_osha        = EXCLUDED.reportable_osha,
            reportable_ansi_r15_08 = EXCLUDED.reportable_ansi_r15_08,
            overall_status         = EXCLUDED.overall_status,
            updated_at             = now()""",
        facility_id,
        now.date(),
        monitored_hours,
        uptime_pct,
        gap_count,
        max_gap_minutes,
        counts["EU_AI_ACT"],
        counts["ISO_10218"],
        counts["OSHA_1910"],
        counts["ANSI_R15_08"],
        overall_status,
    )

    logger.info(
        f"[{facility_id}] uptime={uptime_pct:.1f}% gaps={gap_count} "
        f"status={overall_status} "
        f"reportable=EU:{counts['EU_AI_ACT']} ISO:{counts['ISO_10218']} "
        f"OSHA:{counts['OSHA_1910']} ANSI:{counts['ANSI_R15_08']}"
    )


# ─── Uptime calculation ───────────────────────────────────────────────────────

def _calc_uptime(
    facility_id: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[float, float, int, float]:
    """Return (uptime_pct, monitored_hours, gap_count, max_gap_minutes).

    Queries ClickHouse for per-minute telemetry presence and detects gaps
    longer than _GAP_THRESHOLD. Silence before the first event and after the
    last event is included in the gap accounting.
    """
    client = clickhouse.get_client()
    result = client.query(
        """SELECT toStartOfMinute(timestamp) AS minute
        FROM telemetry
        WHERE facility_id = %(facility_id)s
          AND timestamp >= %(start)s AND timestamp <= %(end)s
        GROUP BY minute ORDER BY minute""",
        parameters={"facility_id": facility_id, "start": window_start, "end": window_end},
    )

    minutes: list[datetime] = []
    for (dt,) in result.result_rows:
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        minutes.append(dt)

    total_seconds = (window_end - window_start).total_seconds()
    if not minutes:
        return 0.0, 0.0, 1, total_seconds / 60

    gaps: list[float] = []  # gap durations in seconds

    def _maybe_gap(a: datetime, b: datetime) -> None:
        delta = b - a
        if delta > _GAP_THRESHOLD:
            gaps.append(delta.total_seconds())

    _maybe_gap(window_start, minutes[0])
    for prev, curr in zip(minutes, minutes[1:]):
        _maybe_gap(prev, curr)
    _maybe_gap(minutes[-1], window_end)

    gap_seconds = sum(gaps)
    monitored_seconds = max(0.0, total_seconds - gap_seconds)
    uptime_pct = round(monitored_seconds / total_seconds * 100, 2)
    monitored_hours = round(monitored_seconds / 3600, 2)
    max_gap_minutes = round(max(gaps) / 60, 1) if gaps else 0.0

    return uptime_pct, monitored_hours, len(gaps), max_gap_minutes


# ─── Reportable incident counting ────────────────────────────────────────────

async def _count_reportable(
    pool,
    facility_id: str,
    start: datetime,
    end: datetime,
    framework: str,
) -> int:
    """Count incidents that are reportable under the given framework."""
    if framework == "EU_AI_ACT":
        row = await pool.fetchrow(
            "SELECT count(*) AS c FROM incidents "
            "WHERE facility_id=$1 AND occurred_at>=$2 AND occurred_at<=$3 "
            "AND reportable_eu_ai_act=true",
            facility_id, start, end,
        )
    elif framework == "OSHA_1910":
        row = await pool.fetchrow(
            "SELECT count(*) AS c FROM incidents "
            "WHERE facility_id=$1 AND occurred_at>=$2 AND occurred_at<=$3 "
            "AND reportable_osha=true",
            facility_id, start, end,
        )
    else:
        row = await pool.fetchrow(
            "SELECT count(*) AS c FROM incidents "
            "WHERE facility_id=$1 AND occurred_at>=$2 AND occurred_at<=$3 "
            "AND regulatory_framework=$4",
            facility_id, start, end, framework,
        )
    return int(row["c"]) if row else 0


# ─── Monthly auto-generation ──────────────────────────────────────────────────

async def _auto_generate_monthly_reports(
    facilities: list[dict],
    now: datetime,
) -> None:
    """On the 1st of each month, generate compliance PDFs for the previous month
    for every framework that has at least one reportable incident."""
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_last_day = (first_of_this_month - timedelta(days=1)).date()
    prev_month_start: date = prev_month_last_day.replace(day=1)
    month_str = prev_month_start.strftime("%Y-%m")

    period_start_dt = datetime(
        prev_month_start.year, prev_month_start.month, prev_month_start.day,
        tzinfo=timezone.utc,
    )
    period_end_dt = datetime(
        first_of_this_month.year, first_of_this_month.month, first_of_this_month.day,
        tzinfo=timezone.utc,
    )

    logger.info(f"Auto-generating compliance reports for {month_str}")
    pool = await postgres.get_pool()

    for f in facilities:
        fid = f["id"]
        for fw in FRAMEWORKS:
            count = await _count_reportable(pool, fid, period_start_dt, period_end_dt, fw)
            if count == 0:
                logger.debug(f"[{fid}] skipping {fw} — no reportable incidents in {month_str}")
                continue
            try:
                path = await generate_compliance_report(fid, fw, prev_month_start, prev_month_last_day)
                logger.info(f"[{fid}] {fw} report generated: {path}")
            except Exception as e:
                logger.error(f"[{fid}] {fw} report generation failed: {e}")

"""Claude API narrative generation for monthly report sections."""

import json
import logging

from app.config import settings

logger = logging.getLogger("intelligence.reports.narrative")

SYSTEM_PROMPT = (
    "You are a safety analytics expert writing a professional monthly report for a "
    "warehouse safety manager. Write concise, factual, actionable prose. Every claim "
    "must be supported by the provided data. No speculation. Use specific numbers. "
    "Keep each section to 2-4 sentences."
)

COMPLIANCE_SYSTEM_PROMPT = (
    "You are a regulatory compliance expert writing a formal compliance monitoring report "
    "for an industrial robotics deployment. Write concise, factual, legally precise prose. "
    "Reference the applicable regulatory framework (EU AI Act, ISO 10218, OSHA, ANSI R15.08) "
    "by name. Every claim must be supported by the provided data. No speculation. "
    "Use specific numbers and timeframes. Keep each section to 3-5 sentences."
)

SECTION_PROMPTS = {
    "executive_summary": (
        "Write a 3-4 sentence executive summary for a monthly safety report. "
        "Highlight the most important findings and overall trend."
    ),
    "hotspot_description": (
        "Describe this spatial hotspot in one paragraph. Include the location, "
        "incident count, dominant event type, and the recommended action."
    ),
    "temporal_analysis": (
        "Write a short paragraph analyzing the temporal patterns in safety incidents. "
        "Highlight any shift-change correlations and day-of-week trends."
    ),
    "robot_health_summary": (
        "Summarize the robot fleet health status. Highlight any robots that need "
        "immediate attention and why."
    ),
    "overall_assessment": (
        "Write a one-paragraph overall safety assessment. Is the facility improving, "
        "stable, or declining? What should be the top priority next month?"
    ),
    "compliance_executive_summary": (
        "Write a 3-5 sentence executive summary for a regulatory compliance report covering "
        "the specified framework and reporting period. State the overall compliance status "
        "(compliant / attention_needed / non_compliant), the compliance score as a percentage, "
        "the number of reportable incidents, and whether all mandatory reporting deadlines were met. "
        "Close with the single most important action item for the compliance officer."
    ),
    "compliance_recommendations": (
        "Write 3-5 concise, prioritised recommendations for improving regulatory compliance. "
        "Each recommendation must be specific, tied to a data point in the provided context "
        "(e.g. a requirement with met/partial/not_met status, an overdue deadline, a monitoring gap), "
        "and reference the applicable regulatory article or clause where relevant. "
        "Format as a numbered list with one sentence per item."
    ),
}


_COMPLIANCE_SECTIONS = {"compliance_executive_summary", "compliance_recommendations"}


async def generate_narrative(section: str, **data) -> str:
    """Generate narrative text for a report section using Claude API.

    Falls back to a template if the API key is not configured or the call fails.
    """
    if not settings.ANTHROPIC_API_KEY:
        return _fallback_narrative(section, data)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = SECTION_PROMPTS.get(section, f"Write about: {section}")
        system = COMPLIANCE_SYSTEM_PROMPT if section in _COMPLIANCE_SECTIONS else SYSTEM_PROMPT
        data_str = json.dumps(data, indent=2, default=str)

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            temperature=0.1,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": f"{prompt}\n\nData:\n{data_str}",
                }
            ],
        )
        text = message.content[0].text
        logger.info(f"Generated {section} narrative ({len(text)} chars)")
        return text

    except Exception as e:
        logger.warning(f"Claude API failed for {section}: {e} — using fallback")
        return _fallback_narrative(section, data)


def _fallback_narrative(section: str, data: dict) -> str:
    """Template-based fallback when Claude API is unavailable."""
    total = data.get("total_incidents", 0)
    prev = data.get("prev_incidents", 0)
    efficiency = data.get("fleet_efficiency_pct", 0)
    time_lost = data.get("total_time_lost_hours", 0)
    hallucinations = data.get("hallucination_count", 0)

    if section == "executive_summary":
        trend = "improving" if total < prev else ("stable" if total == prev else "declining")
        delta = abs(total - prev)
        direction = "fewer" if total < prev else "more"
        return (
            f"This month recorded {total} safety incidents, {delta} {direction} than last month. "
            f"Fleet efficiency was {efficiency:.1f}%, with {time_lost:.1f} hours lost to safety events. "
            f"{hallucinations} sensor hallucinations were identified and excluded. "
            f"The overall safety trend is {trend}."
        )
    if section == "hotspot_description":
        hs = data.get("hotspot", {})
        return (
            f"A cluster of {hs.get('incident_count', 0)} incidents was detected at "
            f"position ({hs.get('center_x', 0):.1f}, {hs.get('center_y', 0):.1f}). "
            f"The dominant event type is {hs.get('dominant_type', 'mixed')}. "
            f"{hs.get('recommendation', 'No specific recommendation.')}"
        )
    if section == "overall_assessment":
        return (
            f"The facility recorded {total} incidents with {efficiency:.1f}% fleet efficiency. "
            f"Focus areas for next month should include addressing top hotspots and "
            f"any robots flagged for maintenance."
        )
    if section == "compliance_executive_summary":
        framework = data.get("framework", "the applicable framework")
        score = data.get("compliance_score", 0.0)
        reportable = data.get("reportable_incidents", 0)
        status = data.get("overall_status", "unknown")
        overdue = data.get("overdue_deadlines", 0)
        deadline_note = (
            f"{overdue} mandatory reporting deadline(s) were missed."
            if overdue > 0
            else "All mandatory reporting deadlines were met within the period."
        )
        return (
            f"This compliance report covers {framework} obligations for the specified reporting period. "
            f"The facility achieved a compliance score of {score:.1f}% with an overall status of {status}. "
            f"{reportable} reportable incident(s) were identified during the period. "
            f"{deadline_note} "
            f"Immediate attention should be directed to any requirements rated 'not_met' or 'partial'."
        )
    if section == "compliance_recommendations":
        framework = data.get("framework", "the applicable framework")
        not_met = data.get("requirements_not_met", [])
        overdue = data.get("overdue_deadlines", 0)
        uptime = data.get("uptime_pct", 0.0)
        recs = []
        if overdue > 0:
            recs.append(
                f"1. Immediately remediate {overdue} overdue {framework} reporting deadline(s) "
                f"by filing the required incident notifications with the relevant authority."
            )
        if uptime < 95:
            recs.append(
                f"{len(recs)+1}. Improve robot telemetry uptime from {uptime:.1f}% to ≥95% "
                f"to meet continuous monitoring obligations under {framework}."
            )
        if not_met:
            first = not_met[0] if isinstance(not_met[0], str) else not_met[0].get("title", "flagged requirement")
            recs.append(
                f"{len(recs)+1}. Address the '{first}' requirement, which is currently not met; "
                f"review the applicable {framework} article and implement corrective controls."
            )
        recs.append(
            f"{len(recs)+1}. Schedule a quarterly compliance review to verify all {framework} "
            f"requirements remain met after any robot configuration or facility changes."
        )
        recs.append(
            f"{len(recs)+1}. Ensure incident classification tags are populated for every safety event "
            f"to maintain an accurate compliance audit trail."
        )
        return "\n".join(recs)
    return f"Analysis complete. {total} incidents processed for this period."

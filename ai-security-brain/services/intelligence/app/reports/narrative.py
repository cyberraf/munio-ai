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
}


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
        data_str = json.dumps(data, indent=2, default=str)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            temperature=0.1,
            system=SYSTEM_PROMPT,
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
    return f"Analysis complete. {total} incidents processed for this period."

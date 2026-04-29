from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ─── Incidents (extended) ────────────────────────────────────────────────────

class IncidentExtended(BaseModel):
    id: UUID
    robot_id: str
    event_type: str
    severity: str
    description: str | None
    distance_cm: float
    speed: float
    steering_angle: float
    occurred_at: datetime
    facility_id: str = ""
    root_cause: str | None = None
    root_cause_confidence: float | None = None
    time_lost_seconds: float | None = None
    is_hallucination: bool = False
    is_prevented: bool = True
    corroborated: bool | None = None


# ─── Hotspots ────────────────────────────────────────────────────────────────

class Hotspot(BaseModel):
    id: UUID | None = None
    facility_id: str
    center_x: float
    center_y: float
    radius_m: float
    incident_count: int
    dominant_type: str | None = None
    dominant_cause: str | None = None
    time_lost_hours: float | None = None
    description: str | None = None
    recommendation: str | None = None
    projected_savings_hours: float | None = None
    status: str = "active"
    first_seen: datetime | None = None
    last_incident: datetime | None = None


class HotspotCreate(BaseModel):
    facility_id: str
    center_x: float
    center_y: float
    radius_m: float
    incident_count: int
    dominant_type: str | None = None
    dominant_cause: str | None = None
    description: str | None = None
    recommendation: str | None = None


# ─── Temporal Patterns ───────────────────────────────────────────────────────

class TemporalPattern(BaseModel):
    id: UUID | None = None
    facility_id: str
    pattern_type: str
    description: str
    hour_of_day: int | None = None
    day_of_week: int | None = None
    incident_rate: float | None = None
    baseline_rate: float | None = None
    multiplier: float | None = None
    recommendation: str | None = None
    detected_at: datetime | None = None


# ─── Robot Health ────────────────────────────────────────────────────────────

class RobotHealth(BaseModel):
    id: UUID | None = None
    robot_id: str
    facility_id: str
    date: date
    incidents_per_hour: float = 0
    sensor_reliability_pct: float = 100
    battery_avg_voltage: float = 0
    battery_trend: float = 0
    behavioral_consistency: float = 100
    hallucination_count: int = 0
    overall_score: float = 100
    flags: list[str] = Field(default_factory=list)


# ─── Recommendations ────────────────────────────────────────────────────────

class Recommendation(BaseModel):
    id: UUID | None = None
    facility_id: str
    category: str
    priority: int
    title: str
    description: str
    evidence: dict[str, Any] | None = None
    projected_time_saved_hours: float | None = None
    projected_cost_saved: float | None = None
    implementation_cost_est: float | None = None
    payback_days: float | None = None
    status: str = "open"
    implemented_at: datetime | None = None
    measured_impact: dict[str, Any] | None = None
    created_at: datetime | None = None


# ─── Monthly Reports ────────────────────────────────────────────────────────

class MonthlyReport(BaseModel):
    id: UUID | None = None
    facility_id: str
    month: date
    total_incidents: int = 0
    total_time_lost_hours: float = 0
    fleet_efficiency_pct: float = 0
    hotspot_count: int = 0
    recommendations_count: int = 0
    report_url: str | None = None
    generated_at: datetime | None = None


# ─── Facility Maps ───────────────────────────────────────────────────────────

class FacilityMap(BaseModel):
    id: UUID | None = None
    facility_id: str
    map_type: str
    source: str | None = None
    resolution: float | None = None
    origin_x: float | None = None
    origin_y: float | None = None
    width_pixels: int | None = None
    height_pixels: int | None = None
    s3_url: str
    is_active: bool = True
    captured_at: datetime | None = None


class MapChange(BaseModel):
    id: UUID | None = None
    facility_id: str
    old_map_id: UUID | None = None
    new_map_id: UUID | None = None
    change_type: str | None = None
    location_x: float | None = None
    location_y: float | None = None
    description: str | None = None
    impact: str | None = None
    detected_at: datetime | None = None


# ─── Incident Snapshots ─────────────────────────────────────────────────────

class IncidentSnapshot(BaseModel):
    id: UUID | None = None
    incident_id: UUID
    robot_id: str
    s3_url: str
    captured_at: datetime
    description: str | None = None


# ─── Productivity ────────────────────────────────────────────────────────────

class ProductivityMetrics(BaseModel):
    facility_id: str
    period_start: datetime
    period_end: datetime
    total_incidents: int
    total_time_lost_hours: float
    hallucination_count: int
    real_incident_count: int
    time_lost_per_robot_hour: float
    fleet_efficiency_pct: float

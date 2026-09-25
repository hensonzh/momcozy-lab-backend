from __future__ import annotations

from datetime import date as DateValue
from datetime import datetime as DateTimeValue
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


MilkAnalysisOperation = Literal["review", "start_or_resume", "answer", "evaluate"]
MilkAnalysisDetailLevel = Literal["summary", "detailed"]


class MilkAnalysisWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(ge=1, le=30, description="Number of recent calendar days included in this snapshot.")
    limit: int = Field(ge=1, le=20, description="Maximum number of recent details shown per record type.")
    include_today: bool = Field(description="Whether the window includes the current local date.")


class MilkAnalysisStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_coverage: str = Field(description="Data coverage status, such as ready, limited, or no_recent_data.")
    pumping_trend: str = Field(description="Trend in measured maternal pumping output, such as increasing, stable, or decreasing.")
    measured_only: bool = Field(description="Whether the trend uses only actual pumping records with measured volumes.")


class MilkAnalysisCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infants: int = Field(ge=0, description="Number of babies in the current user’s profile.")
    recent_feedings: int = Field(ge=0, description="Number of recent actual baby-feeding records in this snapshot.")
    recent_pumpings: int = Field(ge=0, description="Number of recent actual maternal pumping records in this snapshot.")
    trend_days: int = Field(ge=0, description="Number of calendar days returned in the pumping trend.")
    days_with_pumping: int = Field(ge=0, description="Number of days in the trend window with at least one pumping record.")
    trend_pumping_count: int = Field(ge=0, description="Number of pumping sessions with measured volumes in the trend window.")
    recent_growth: int | None = Field(
        default=None,
        ge=0,
        description="Number of recent baby growth records read in detailed mode; null or omitted in summary mode.",
    )


class MilkAnalysisVolumes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recent_feeding_volume_ml: float = Field(ge=0, description="Total explicitly recorded baby intake from recent feeding records, in ml.")
    recent_pumped_volume_ml: float = Field(ge=0, description="Total explicitly measured maternal pumping output in recent details, in ml.")
    trend_pumped_volume_ml: float = Field(ge=0, description="Total maternal pumping output over the trend window, in ml.")
    average_daily_pumped_volume_ml: float = Field(
        ge=0,
        description="Average daily maternal pumping output across all days in the trend window, in ml.",
    )


class MilkAnalysisLatestEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeding_at: DateTimeValue | None = Field(default=None, description="Actual time of the most recent baby feeding; null when no record is available.")
    pumping_at: DateTimeValue | None = Field(default=None, description="Actual time of the most recent maternal pumping session; null when no record is available.")


class MilkAnalysisFeedingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(description="Stable UUID of the actual baby-feeding record.")
    infant_id: UUID | None = Field(default=None, description="UUID of the baby associated with this feeding; null when unspecified.")
    feed_time: DateTimeValue = Field(description="Actual time of this feeding.")
    feed_type: str = Field(description="Feeding method.")
    feed_action: str = Field(description="Feeding action.")
    volume_ml: float | None = Field(default=None, ge=0, description="Explicitly recorded baby intake in ml; null when unknown.")
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Duration of this feeding in seconds; null when unknown.",
    )
    title: str = Field(description="Title of this feeding record.")


class MilkAnalysisPumpingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(description="Stable UUID of the actual maternal pumping record.")
    pump_start_time: DateTimeValue = Field(description="Actual start time of this pumping session.")
    pump_end_time: DateTimeValue | None = Field(default=None, description="Actual end time of this pumping session; null when unknown.")
    milk_volume_ml: float | None = Field(default=None, ge=0, description="Explicitly measured pumped milk volume in ml; null when unknown.")
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Duration of this pumping session in seconds; null when unknown.",
    )
    pump_type: str = Field(description="Pumping method or device type.")
    source: str = Field(description="Pumping record source, such as manual, agent, or device.")
    title: str = Field(description="Title of this pumping record.")


class MilkAnalysisGrowthRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(description="Stable UUID of the baby growth measurement.")
    infant_id: UUID | None = Field(default=None, description="UUID of the baby associated with this measurement; null when unspecified.")
    measured_at: DateTimeValue = Field(description="Actual time of a length, weight, or head circumference measurement.")
    height_cm: float | None = Field(default=None, ge=0, description="Baby’s measured length in cm; null when not measured.")
    weight_kg: float | None = Field(default=None, ge=0, description="Baby’s measured weight in kg; null when not measured.")
    head_cm: float | None = Field(default=None, ge=0, description="Baby’s measured head circumference in cm; null when not measured.")


class MilkAnalysisTrendDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: DateValue = Field(description="Local calendar date represented by this pumping trend entry.")
    pumped_milk_volume_ml: float = Field(ge=0, description="Total maternal pumping output measured on this date, in ml.")
    pumping_count: int = Field(ge=0, description="Number of pumping sessions with measured volumes on this date.")
    measured_only: bool = Field(description="Whether this date’s totals include only actual records with measured volumes.")


class MilkAnalysisPumpingRhythm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(description="IANA timezone used for representative pumping times.")
    representative_date: DateValue | None = Field(
        default=None,
        description="Record date used to select representative pumping times; null when no pumping record is available.",
    )
    representative_times: list[str] = Field(description="Distinct, sorted local pumping times on the representative date, formatted HH:MM.")


class MilkAnalysisInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathway: str = Field(description="Suggested analysis pathway based on deterministic facts available now.")
    data_coverage: str = Field(description="Data coverage used to select the analysis pathway.")
    pumping_trend: str = Field(description="Maternal pumping output trend used to select the analysis pathway.")
    has_recent_growth: bool = Field(description="Whether the snapshot includes at least one recent baby growth record.")
    missing_inputs: list[str] = Field(description="Stable codes for inputs still missing or requiring attention.")
    recommended_next_step: str = Field(description="Suggested next data collection, assessment, or safe support step based on this snapshot.")


class MilkAnalysisReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail_level: MilkAnalysisDetailLevel = Field(description="Whether this response is a status summary or a detailed analysis snapshot.")
    window: MilkAnalysisWindow = Field(description="Statistical window used for this snapshot.")
    status: MilkAnalysisStatus = Field(description="Data coverage and pumping trend status calculated from actual records.")
    counts: MilkAnalysisCounts = Field(description="Counts of babies, records, and trend days in this snapshot.")
    volumes: MilkAnalysisVolumes = Field(description="Deterministic totals for baby intake and maternal pumping output.")
    latest: MilkAnalysisLatestEvents = Field(description="Actual times of the most recent feeding and pumping records.")
    observation_flags: list[str] = Field(description="Stable codes for missing information in the profile or actual records.")
    recent_feedings: list[MilkAnalysisFeedingRecord] | None = Field(
        default=None,
        description="Recent actual baby-feeding records in detailed mode; null or omitted in summary mode.",
    )
    recent_pumpings: list[MilkAnalysisPumpingRecord] | None = Field(
        default=None,
        description="Recent actual maternal pumping records in detailed mode; null or omitted in summary mode.",
    )
    pumping_rhythm: MilkAnalysisPumpingRhythm | None = Field(
        default=None,
        description="Representative pumping rhythm drawn from the full window in detailed mode; null or omitted in summary mode.",
    )
    recent_growth: list[MilkAnalysisGrowthRecord] | None = Field(
        default=None,
        description="Recent baby growth measurements in detailed mode; null or omitted in summary mode.",
    )
    pumping_trends: list[MilkAnalysisTrendDay] | None = Field(
        default=None,
        description="Daily pumping output trend in detailed mode; null or omitted in summary mode.",
    )
    analysis: MilkAnalysisInterpretation | None = Field(
        default=None,
        description="Analysis pathway and next step derived from the deterministic snapshot in detailed mode; null or omitted in summary mode.",
    )


class MilkAnalysisProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, description="Current position in the six-item milk supply assessment intake.")
    total: int = Field(ge=1, description="Total number of intake items required for milk supply assessment.")
    completed_count: int = Field(ge=0, description="Number of intake items with trustworthy user answers.")
    remaining_count: int = Field(ge=0, description="Number of intake items still requiring trustworthy user answers.")


class MilkAnalysisWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_state_id: UUID = Field(description="Stable UUID of the milk supply intake state persisted for this thread.")
    workflow_phase: str = Field(description="Current phase of the milk supply assessment workflow.")
    current_field: str | None = Field(default=None, description="Intake field awaiting the user’s answer; null after intake is complete.")
    next_question: str = Field(description="Next question to show directly to the user; empty when no further question is needed.")
    progress: MilkAnalysisProgress = Field(description="Progress through the six milk supply intake items.")
    can_evaluate: bool = Field(description="Whether the collected information is sufficient for a deterministic assessment.")


class MilkAnalysisEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_state_id: UUID = Field(description="UUID of the persisted milk supply workflow state used for this assessment.")
    artifact_id: UUID = Field(description="UUID of the generated or reused milk supply assessment card.")
    artifact_type: str = Field(description="Type of generated artifact, currently milk_analysis_card.")
    maternal_red_flags: bool = Field(description="Whether the current answers contain maternal breast or systemic danger signs requiring priority care.")
    infant_intake_risk: bool = Field(description="Whether the current answers contain baby intake or growth concerns requiring priority review.")
    data_coverage: str = Field(description="Recent record coverage used in this combined assessment.")
    pumping_trend: str = Field(description="Maternal pumping output trend identified in this combined assessment.")
    replayed: bool = Field(description="Whether the result reused an assessment card already generated for this workflow.")


class MilkAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="Stable result status for this analysis operation.")
    operation: MilkAnalysisOperation = Field(description="Milk supply analysis operation performed.")
    review: MilkAnalysisReview | None = Field(
        default=None,
        description="Deterministic status or detailed snapshot for operation=review; null or omitted otherwise.",
    )
    workflow: MilkAnalysisWorkflow | None = Field(
        default=None,
        description="Persisted workflow progress after starting, resuming, or answering intake; null or omitted otherwise.",
    )
    evaluation: MilkAnalysisEvaluation | None = Field(
        default=None,
        description="Assessment card and risk/trend conclusions for operation=evaluate; null or omitted otherwise.",
    )

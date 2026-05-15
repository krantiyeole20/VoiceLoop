"""
VoiceLoop — Stage Registry

The single source of truth for interview structure. Defines:

  * StageType   — the 12 structural archetypes every track is composed of.
  * TrackType   — the six tracks VoiceLoop ships.
  * StageConfig — one frozen dataclass instance per stage in a track sequence.
  * StageRegistry — static mapping TrackType → ordered List[StageConfig].
  * AGENT_NOTE_SCHEMAS — required note-field schema per StageType. Validated at
                          transition_stage() time. Failure is flagged in eval but
                          never blocks transitions.

Phase 1 ships only the Intro Call sequence. Phase 2 fills out the remaining
five tracks. New tracks are added by appending one List[StageConfig] here —
no other file changes.

See INIT.md §4 for design rationale (stage collapse, params schemas).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# =============================================================================
# Enums
# =============================================================================

class StageType(Enum):
    """Twelve structural archetypes. Every stage in every track is one of these."""
    WELCOME            = "welcome"
    SELF_INTRO         = "self_intro"
    DEPTH_STAGE        = "depth_stage"
    COMPANY_FIT        = "company_fit"
    BEHAVIORAL_Q       = "behavioral_q"
    TECHNICAL_CONCEPTS = "technical_concepts"
    SYSTEM_DESIGN      = "system_design"
    SQL_PROBLEM        = "sql_problem"
    BUSINESS_CASE      = "business_case"
    PRODUCT_SENSE      = "product_sense"
    ANALYTICAL_METRICS = "analytical_metrics"
    CLOSING            = "closing"


class TrackType(Enum):
    """The six interview tracks VoiceLoop ships."""
    INTRO         = "intro"
    BEHAVIORAL    = "behavioral"             # Phase 2
    TECHNICAL_SWE = "technical_swe"          # Phase 2
    DS_ML         = "ds_ml"                  # Phase 2
    ANALYTICS     = "analytics"              # Phase 2
    PRODUCT       = "product"                # Phase 2


# =============================================================================
# Agent Note Schemas — one entry per StageType
#
# These are field_name → type-name strings; the agent's tool argument is
# validated against these at transition time. See agent_worker.transition_stage.
# =============================================================================

AGENT_NOTE_SCHEMAS: dict[StageType, dict[str, str]] = {
    StageType.WELCOME: {
        "candidate_state_observed": "str",     # "calm" | "nervous" | "uncertain"
        "tone_match_confirmed": "bool",
    },
    StageType.SELF_INTRO: {
        "narrative_summary": "str",
        "named_companies_or_projects": "list[str]",
        "contradictions_with_resume": "list[str]",
        "threads_opened_not_probed": "list[str]",
        "transition_reason": "str",            # "time_pressure" | "depth_achieved" | "min_met"
    },
    StageType.DEPTH_STAGE: {
        "project_or_role_discussed": "str",
        "impact_claims_made": "list[str]",
        "threads_opened_not_probed": "list[str]",
        "depth_assessment": "str",             # "surface" | "moderate" | "deep"
        "transition_reason": "str",
        "flags_for_later_stages": "list[str]",
    },
    StageType.COMPANY_FIT: {
        "motivation_themes": "list[str]",
        "company_facts_cited": "list[str]",
        "candidate_questions_asked": "list[str]",
        "alignment_assessment": "str",         # "weak" | "moderate" | "strong"
        "transition_reason": "str",
    },
    StageType.BEHAVIORAL_Q: {
        "competency_targeted": "str",
        "question_asked": "str",
        "star_components_covered": "dict",     # {"S":bool,"T":bool,"A":bool,"R":bool}
        "agency_signal": "str",                # "I" | "we" | "unclear"
        "impact_quantified": "bool",
        "second_example_asked": "bool",
        "transition_reason": "str",
    },
    StageType.TECHNICAL_CONCEPTS: {
        "topics_covered": "list[str]",
        "depth_assessment_per_topic": "dict",  # {topic: "surface"|"moderate"|"deep"}
        "candidate_uncertainty_moments": "list[str]",
        "transition_reason": "str",
    },
    StageType.SYSTEM_DESIGN: {
        "problem_framed": "str",
        "components_discussed": "list[str]",
        "tradeoffs_named_by_candidate": "list[str]",
        "bottlenecks_identified_by_candidate": "list[str]",
        "scale_pushed_to": "str",
        "transition_reason": "str",
    },
    StageType.SQL_PROBLEM: {
        "problem_summary": "str",
        "approach_clarity_before_writing": "bool",
        "edge_cases_candidate_named": "list[str]",
        "optimization_discussed": "bool",
        "transition_reason": "str",
    },
    StageType.BUSINESS_CASE: {
        "case_summary": "str",
        "frameworks_used_by_candidate": "list[str]",
        "metrics_proposed_by_candidate": "list[str]",
        "assumptions_stated": "list[str]",
        "recommendation_clarity": "str",       # "clear" | "hedged" | "absent"
        "transition_reason": "str",
    },
    StageType.PRODUCT_SENSE: {
        "problem_brief": "str",
        "user_segments_identified": "list[str]",
        "features_proposed": "list[str]",
        "prioritization_framework_used": "str",
        "transition_reason": "str",
    },
    StageType.ANALYTICAL_METRICS: {
        "primary_metrics_proposed": "list[str]",
        "counter_metrics_proposed": "list[str]",
        "leading_vs_lagging_distinguished": "bool",
        "transition_reason": "str",
    },
    StageType.CLOSING: {
        "candidate_questions_asked": "list[str]",
        "wrap_completed_naturally": "bool",
    },
}


# =============================================================================
# StageConfig
# =============================================================================

@dataclass(frozen=True)
class StageConfig:
    """
    One stage in one track sequence. Frozen — never mutated at runtime; per-session
    runtime state lives in InterviewState.

    `params` is the only typed-loose field. Schema varies by stage_type; see
    INIT.md §4.2 for the per-StageType params contract.
    """
    stage_id: str
    stage_type: StageType
    display_name: str
    time_limit: int            # seconds
    min_questions: int
    params: dict              # type-specific. See INIT.md §4.2.
    prompt_template_key: str   # name of template class in prompts.py
    eval_rubric_key: str       # name of rubric class in eval_rubrics.py (Phase 3+)
    agent_note_schema: dict    # = AGENT_NOTE_SCHEMAS[stage_type]
    is_shared: bool = False    # True for WELCOME, SELF_INTRO, CLOSING

    def __post_init__(self) -> None:
        # Sanity: agent_note_schema must match the stage_type's canonical schema.
        canonical = AGENT_NOTE_SCHEMAS.get(self.stage_type)
        if canonical is not None and self.agent_note_schema != canonical:
            raise ValueError(
                f"StageConfig({self.stage_id}) agent_note_schema does not match "
                f"AGENT_NOTE_SCHEMAS[{self.stage_type.value}]. Use the canonical."
            )


def _schema_for(stage_type: StageType) -> dict:
    """Helper to fetch the canonical AGENT_NOTE schema."""
    return dict(AGENT_NOTE_SCHEMAS[stage_type])


# =============================================================================
# Track Sequences
# =============================================================================

# ── Track 1: Intro Call ─────────────────────────────────────────────────────
INTRO_SEQUENCE: list[StageConfig] = [
    StageConfig(
        stage_id="welcome",
        stage_type=StageType.WELCOME,
        display_name="Welcome",
        time_limit=60,
        min_questions=1,
        params={
            "track_name": "Intro Call",
            "tone": "warm professional",
            "framework_hint": None,
            "document_injection": [],
        },
        prompt_template_key="WELCOME_TEMPLATE",
        eval_rubric_key="WELCOME_RUBRIC",
        agent_note_schema=_schema_for(StageType.WELCOME),
        is_shared=True,
    ),
    StageConfig(
        stage_id="self_intro",
        stage_type=StageType.SELF_INTRO,
        display_name="Introduction",
        time_limit=120,
        min_questions=2,
        params={
            "depth_expectation": "brief",
            "focus_hint": "motivation + background",
            "document_injection": [],
        },
        prompt_template_key="SELF_INTRO_TEMPLATE",
        eval_rubric_key="SELF_INTRO_RUBRIC",
        agent_note_schema=_schema_for(StageType.SELF_INTRO),
        is_shared=True,
    ),
    StageConfig(
        stage_id="depth_general",
        stage_type=StageType.DEPTH_STAGE,
        display_name="Experience",
        time_limit=240,
        min_questions=5,
        params={
            "focus_area": "general",
            "document_injection": ["resume"],
        },
        prompt_template_key="DEPTH_STAGE_TEMPLATE",
        eval_rubric_key="DEPTH_STAGE_RUBRIC",
        agent_note_schema=_schema_for(StageType.DEPTH_STAGE),
    ),
    StageConfig(
        stage_id="company_fit",
        stage_type=StageType.COMPANY_FIT,
        display_name="Company Fit",
        time_limit=240,
        min_questions=3,
        params={
            "document_injection": ["resume", "jd"],
        },
        prompt_template_key="COMPANY_FIT_TEMPLATE",
        eval_rubric_key="COMPANY_FIT_RUBRIC",
        agent_note_schema=_schema_for(StageType.COMPANY_FIT),
    ),
    StageConfig(
        stage_id="closing",
        stage_type=StageType.CLOSING,
        display_name="Closing",
        time_limit=45,
        min_questions=0,
        params={
            "track_name": "Intro Call",
            "follow_up_allowed": True,
        },
        prompt_template_key="CLOSING_TEMPLATE",
        eval_rubric_key="CLOSING_RUBRIC",
        agent_note_schema=_schema_for(StageType.CLOSING),
        is_shared=True,
    ),
]


# Phase 2: populate these.
BEHAVIORAL_SEQUENCE: list[StageConfig] = []
TECHNICAL_SWE_SEQUENCE: list[StageConfig] = []
DS_ML_SEQUENCE: list[StageConfig] = []
ANALYTICS_SEQUENCE: list[StageConfig] = []
PRODUCT_SEQUENCE: list[StageConfig] = []


# =============================================================================
# StageRegistry — static dispatch
# =============================================================================

class StageRegistry:
    """
    Static registry. Maps TrackType to its ordered list of StageConfig.

    Phase 1 implements only INTRO. Phase 2 fills in the rest.
    """

    _SEQUENCES: dict[TrackType, list[StageConfig]] = {
        TrackType.INTRO:         INTRO_SEQUENCE,
        TrackType.BEHAVIORAL:    BEHAVIORAL_SEQUENCE,
        TrackType.TECHNICAL_SWE: TECHNICAL_SWE_SEQUENCE,
        TrackType.DS_ML:         DS_ML_SEQUENCE,
        TrackType.ANALYTICS:     ANALYTICS_SEQUENCE,
        TrackType.PRODUCT:       PRODUCT_SEQUENCE,
    }

    _DISPLAY_NAMES: dict[TrackType, str] = {
        TrackType.INTRO:         "Intro Call",
        TrackType.BEHAVIORAL:    "Behavioral Interview",
        TrackType.TECHNICAL_SWE: "Technical Interview (SWE)",
        TrackType.DS_ML:         "Technical Interview (Data Science / ML)",
        TrackType.ANALYTICS:     "Analytics / BI Interview",
        TrackType.PRODUCT:       "Product / Strategy Interview",
    }

    @classmethod
    def get_stages(cls, track: TrackType) -> list[StageConfig]:
        """Return the ordered StageConfig list for a track. Empty list = unimplemented."""
        return cls._SEQUENCES.get(track, [])

    @classmethod
    def get_stage_ids(cls, track: TrackType) -> list[str]:
        return [s.stage_id for s in cls.get_stages(track)]

    @classmethod
    def get_stage_by_id(cls, track: TrackType, stage_id: str) -> StageConfig | None:
        for s in cls.get_stages(track):
            if s.stage_id == stage_id:
                return s
        return None

    @classmethod
    def get_display_name(cls, track: TrackType) -> str:
        return cls._DISPLAY_NAMES.get(track, track.value)

    @classmethod
    def is_implemented(cls, track: TrackType) -> bool:
        """True if the track has at least one stage defined."""
        return len(cls.get_stages(track)) > 0

    @classmethod
    def implemented_tracks(cls) -> list[TrackType]:
        return [t for t in TrackType if cls.is_implemented(t)]


# =============================================================================
# Validators (callable from agent_worker.transition_stage)
# =============================================================================

# Map our string type names to Python types for runtime isinstance checks.
_TYPE_MAP = {
    "str":       str,
    "bool":      bool,
    "int":       int,
    "float":     float,
    "dict":      dict,
    "list":      list,
    "list[str]": list,    # element type checked separately below
    "list[int]": list,
}


def validate_agent_note(stage_type: StageType, note_fields: dict) -> tuple[bool, list[str]]:
    """
    Validate a candidate agent_note dict against its canonical schema.

    Returns (is_valid, error_messages). is_valid is True only if every required
    field is present AND has the expected type. Extra fields are allowed
    (they're preserved in stage_notes for downstream eval consumption).
    """
    schema = AGENT_NOTE_SCHEMAS.get(stage_type)
    if schema is None:
        return False, [f"No schema registered for stage_type={stage_type.value}"]
    if not isinstance(note_fields, dict):
        return False, [f"agent_note must be a dict, got {type(note_fields).__name__}"]

    errors: list[str] = []
    for field_name, type_name in schema.items():
        if field_name not in note_fields:
            errors.append(f"missing required field: '{field_name}' ({type_name})")
            continue

        value = note_fields[field_name]
        expected = _TYPE_MAP.get(type_name)
        if expected is None:
            errors.append(f"unknown type spec '{type_name}' for field '{field_name}'")
            continue

        if not isinstance(value, expected):
            errors.append(
                f"field '{field_name}' expected {type_name}, got {type(value).__name__}"
            )
            continue

        # Soft element-type check for list[str]
        if type_name == "list[str]" and not all(isinstance(x, str) for x in value):
            errors.append(f"field '{field_name}' must contain only strings")

    return (len(errors) == 0), errors

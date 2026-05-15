"""
VoiceLoop — Generic Interview FSM

One InterviewState class handles every track. The state advances through a
List[StageConfig] held in `stage_sequence` (injected at session start from
`stage_registry.StageRegistry.get_stages(track)`).

There are NO per-track subclasses, NO per-track enums, and NO hardcoded stage
names in this module — all of that lives in `stage_registry.py`.

See INIT.md §3.1 for design rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from stage_registry import StageConfig, StageRegistry, StageType, TrackType

logger = logging.getLogger(__name__)


# =============================================================================
# AgentNote — written at every transition_stage() call
# =============================================================================

@dataclass
class AgentNote:
    """
    Structured note the agent writes immediately before each stage transition.
    Validated against StageConfig.agent_note_schema by transition_stage().

    `fields` holds the raw note dict (per AGENT_NOTE_SCHEMAS[stage_type]).
    `schema_valid` is True only if every required field is present + typed correctly.
    `schema_errors` accumulates per-field error messages for the eval pipeline.
    """
    stage_id: str
    stage_type: StageType
    fields: dict
    schema_valid: bool = True
    schema_errors: list[str] = field(default_factory=list)
    written_at: str = ""

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "stage_type": self.stage_type.value,
            "fields": self.fields,
            "schema_valid": self.schema_valid,
            "schema_errors": self.schema_errors,
            "written_at": self.written_at,
        }


# =============================================================================
# InterviewState
# =============================================================================

@dataclass
class InterviewState:
    """
    Per-session mutable state. Lives in the AgentSession's `userdata`.

    Lifecycle:
      1. agent_worker.run_interview() instantiates this with stage_sequence
         from StageRegistry.get_stages(track) and candidate metadata.
      2. As stages run, `current_index` advances via transition_to().
      3. `stage_notes` accumulates an AgentNote per stage transition.
      4. On session end, the relevant fields are serialized to interviews row
         (conversation + stage_notes + track + track_config).
    """

    # --- Sequence reference -------------------------------------------------
    track: TrackType = TrackType.INTRO
    stage_sequence: list[StageConfig] = field(default_factory=list)
    current_index: int = 0

    # --- Candidate identity / role ------------------------------------------
    candidate_name: str = ""
    candidate_email: str = ""
    job_role: str = ""
    experience_level: str = ""
    company_name: Optional[str] = None
    company_context: Optional[str] = None

    # --- Document context (RAG) ---------------------------------------------
    uploaded_resume_text: Optional[str] = None
    job_description: Optional[str] = None
    portfolio_text: Optional[str] = None
    include_profile: bool = True

    # --- Stage timing + progress -------------------------------------------
    stage_started_at: Optional[datetime] = None
    last_state_verification: Optional[datetime] = None
    questions_asked: list[str] = field(default_factory=list)
    questions_per_stage: dict[str, int] = field(default_factory=dict)
    transition_count: int = 0
    forced_transitions: int = 0
    skipped_stages: list[str] = field(default_factory=list)

    # --- Pending acknowledgement (queued mid-user-speech transitions) ------
    pending_acknowledgement: Optional[str] = None
    pending_ack_stage: Optional[str] = None
    transition_acknowledged: bool = False

    # --- Closing safety ----------------------------------------------------
    closing_initiated: bool = False
    closing_message_delivered: bool = False

    # --- Assessment log (assess_response calls) — programmatic eval signal -
    assessments: list[dict] = field(default_factory=list)

    # --- Agent notes per stage (load-bearing for eval) ---------------------
    stage_notes: dict[str, AgentNote] = field(default_factory=dict)

    # --- Skip queue --------------------------------------------------------
    skip_stage_queue: list[str] = field(default_factory=list)

    # --- Bookkeeping (set during finalize) ---------------------------------
    _user_id: Optional[str] = None
    _interview_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Stage navigation
    # ------------------------------------------------------------------

    @property
    def current_stage(self) -> StageConfig:
        """The currently-active StageConfig. Raises IndexError if mis-initialised."""
        if not self.stage_sequence:
            raise RuntimeError("InterviewState.stage_sequence is empty")
        return self.stage_sequence[self.current_index]

    def get_next_stage(self) -> Optional[StageConfig]:
        """Return the next StageConfig in sequence, or None if at the final stage."""
        next_idx = self.current_index + 1
        if next_idx >= len(self.stage_sequence):
            return None
        return self.stage_sequence[next_idx]

    def get_stage_by_id(self, stage_id: str) -> Optional[StageConfig]:
        for s in self.stage_sequence:
            if s.stage_id == stage_id:
                return s
        return None

    def can_skip_to(self, target_stage_id: str) -> bool:
        """Skips are forward-only within the current sequence."""
        target_idx = None
        for i, s in enumerate(self.stage_sequence):
            if s.stage_id == target_stage_id:
                target_idx = i
                break
        if target_idx is None:
            return False
        return target_idx > self.current_index

    def queue_skip_to(self, target_stage_id: str) -> bool:
        if not self.can_skip_to(target_stage_id):
            logger.warning(
                f"[FSM] Cannot skip to '{target_stage_id}' from '{self.current_stage.stage_id}'"
            )
            return False
        self.skip_stage_queue.append(target_stage_id)
        logger.info(f"[FSM] Queued skip to '{target_stage_id}'")
        return True

    def process_skip_queue(self) -> Optional[StageConfig]:
        while self.skip_stage_queue:
            target_id = self.skip_stage_queue.pop(0)
            if self.can_skip_to(target_id):
                return self.get_stage_by_id(target_id)
        return None

    def can_transition(self) -> bool:
        return self.get_next_stage() is not None

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def transition_to(
        self,
        target: StageConfig,
        forced: bool = False,
        skipped: bool = False,
    ) -> None:
        """
        Move current_index to point at `target`. Caller is responsible for
        having already validated that `target` is in `stage_sequence`.

        Side effects:
          * stage_started_at, last_state_verification reset to now()
          * transition_count increments; forced/skipped counters as applicable
          * pending_acknowledgement state cleared (caller may re-queue)
          * closing flags reset if leaving CLOSING (defensive — shouldn't happen)
        """
        try:
            target_index = self.stage_sequence.index(target)
        except ValueError:
            raise ValueError(
                f"transition_to: target stage '{target.stage_id}' not in sequence"
            )

        old_stage = self.current_stage
        old_index = self.current_index
        self.current_index = target_index
        self.stage_started_at = datetime.now()
        self.last_state_verification = datetime.now()
        self.transition_count += 1

        # Reset ack tracking for new transition
        self.transition_acknowledged = False

        if target.stage_type != StageType.CLOSING:
            self.closing_initiated = False
            self.closing_message_delivered = False

        if forced:
            self.forced_transitions += 1

        if skipped:
            self.skipped_stages.append(old_stage.stage_id)

        logger.info(
            f"[FSM] Stage transition: {old_stage.stage_id} -> {target.stage_id} "
            f"(idx {old_index}→{target_index}, forced={forced}, skipped={skipped}, "
            f"total_transitions={self.transition_count})"
        )

    # ------------------------------------------------------------------
    # Time / question tracking
    # ------------------------------------------------------------------

    def verify_state(self) -> StageConfig:
        self.last_state_verification = datetime.now()
        return self.current_stage

    def time_in_current_stage(self) -> float:
        if not self.stage_started_at:
            return 0.0
        return (datetime.now() - self.stage_started_at).total_seconds()

    def time_since_verification(self) -> float:
        if not self.last_state_verification:
            return 0.0
        return (datetime.now() - self.last_state_verification).total_seconds()

    def get_stage_time_limit(self) -> int:
        return self.current_stage.time_limit

    def get_time_status(self) -> dict:
        limit = self.get_stage_time_limit()
        elapsed = self.time_in_current_stage()
        remaining = max(0.0, limit - elapsed)
        elapsed_pct = min(100.0, (elapsed / limit) * 100) if limit > 0 else 0.0
        remaining_pct = max(0.0, 100.0 - elapsed_pct)
        return {
            "elapsed": elapsed,
            "limit": limit,
            "remaining_seconds": remaining,
            "elapsed_pct": elapsed_pct,
            "remaining_pct": remaining_pct,
            "is_overtime": elapsed > limit,
        }

    def get_question_status(self) -> dict:
        stage_id = self.current_stage.stage_id
        asked = self.questions_per_stage.get(stage_id, 0)
        minimum = self.current_stage.min_questions
        return {
            "asked": asked,
            "minimum": minimum,
            "met_minimum": asked >= minimum,
            "remaining_to_min": max(0, minimum - asked),
        }

    def should_transition_soon(self) -> bool:
        q = self.get_question_status()
        t = self.get_time_status()
        return q["met_minimum"] and t["elapsed_pct"] >= 50

    def get_progress_summary(self) -> str:
        t = self.get_time_status()
        q = self.get_question_status()
        remaining_pct = t["remaining_pct"]
        remaining_sec = t["remaining_seconds"]

        if remaining_pct <= 10:
            urgency = "CRITICAL"
        elif remaining_pct <= 25:
            urgency = "HIGH"
        elif remaining_pct <= 50:
            urgency = "MODERATE"
        else:
            urgency = "LOW"

        return (
            f"[PROGRESS] Stage: {self.current_stage.stage_id} | "
            f"Questions: {q['asked']}/{q['minimum']} min | "
            f"Time: {remaining_pct:.0f}% remaining ({remaining_sec:.0f}s) | "
            f"Urgency: {urgency}"
        )

    # ------------------------------------------------------------------
    # Document context (resume / JD) — driven by StageConfig.params
    # ------------------------------------------------------------------

    def get_document_context(self) -> str:
        """
        Render the document context block for the current stage based on
        its params['document_injection'] list. Returns "" if include_profile
        is False or no documents are configured for this stage.

        document_injection values:
          "resume" → injects uploaded_resume_text (truncated to 1500 chars)
          "jd"     → injects job_description (truncated to 1000 chars)
        """
        if not self.include_profile:
            return ""

        injection_keys = self.current_stage.params.get("document_injection", []) or []
        parts: list[str] = []

        if "resume" in injection_keys and self.uploaded_resume_text:
            snippet = self.uploaded_resume_text[:1500]
            if len(self.uploaded_resume_text) > 1500:
                snippet += "..."
            parts.append(
                "CANDIDATE RESUME HIGHLIGHTS:\n"
                f"{snippet}\n\n"
                "INSTRUCTION: Reference specific projects, skills, and experiences "
                "from the resume when asking follow-up questions. Ask about gaps, "
                "challenges faced, and technical details mentioned."
            )

        if "jd" in injection_keys and self.job_description:
            snippet = self.job_description[:1000]
            if len(self.job_description) > 1000:
                snippet += "..."
            parts.append(
                "JOB DESCRIPTION:\n"
                f"{snippet}\n\n"
                "INSTRUCTION: Assess how the candidate's background and interests "
                "align with this role's requirements. Tie questions to the specific "
                "expectations described."
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        t = self.get_time_status()
        q = self.get_question_status()
        return {
            "track": self.track.value,
            "stage": self.current_stage.stage_id,
            "stage_index": self.current_index,
            "candidate_name": self.candidate_name,
            "job_role": self.job_role,
            "experience_level": self.experience_level,
            "time_in_stage": t["elapsed"],
            "time_remaining_pct": t["remaining_pct"],
            "questions_asked": q["asked"],
            "questions_minimum": q["minimum"],
            "transition_count": self.transition_count,
            "forced_transitions": self.forced_transitions,
            "skipped_stages": self.skipped_stages,
            "has_resume": bool(self.uploaded_resume_text),
            "has_jd": bool(self.job_description),
            "include_profile": self.include_profile,
            "stage_notes_count": len(self.stage_notes),
        }

    def stage_notes_to_dict(self) -> dict:
        """Serialise stage_notes for persistence in interviews.stage_notes jsonb."""
        return {sid: note.to_dict() for sid, note in self.stage_notes.items()}


# =============================================================================
# Factory
# =============================================================================

def build_interview_state(track: TrackType) -> InterviewState:
    """
    Convenience factory: instantiate a fresh InterviewState bound to a track's
    sequence. Caller fills candidate identity fields after construction.
    """
    sequence = StageRegistry.get_stages(track)
    if not sequence:
        raise ValueError(
            f"Track '{track.value}' has no stage sequence in StageRegistry. "
            f"Is it implemented yet?"
        )
    return InterviewState(track=track, stage_sequence=list(sequence), current_index=0)

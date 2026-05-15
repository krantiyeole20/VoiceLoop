"""
VoiceLoop — Interview Agent Worker (subprocess)

One subprocess per interview session. Connects DIRECTLY to a LiveKit room
(no dispatch). BYOK keys come in via environment from worker_manager.py.

What's adapted from MockFlow vs. what's new:
  * Generic FSM-driven instruction building (stage_registry.StageRegistry +
    prompts.build_stage_instructions). NO per-track branching in this file.
  * transition_stage tool accepts an `agent_note` dict argument and validates
    it against the current StageConfig.agent_note_schema. Notes persist into
    InterviewState.stage_notes.
  * Eval pipeline trigger is STUBBED in Phase 1 — logs only.
  * AgentSession uses the spec-mandated VAD/turn-detection settings (0.5/3.0).

Voice correctness solved problems are carried over verbatim:
  - 8s Silero readiness wait (in worker_manager.py)
  - VAD endpointing 0.5 / 3.0
  - Interruption: min_interruption_duration=0.5, discard_audio_if_uninterruptible=True
  - Question dedup ("DUPLICATE:" / "SIMILAR:" prefix)
  - Fallback timer with 50/75/90/100 milestone logging, asyncio.CancelledError handling
  - CLOSING content detection + 5s sleep before disconnect
  - Every @function_tool has try/except returning a string
  - Direct OpenAI calls (if any) use timeout=10
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Annotated, Any, Optional

import aiohttp
from pydantic import Field

from livekit import api as livekit_api
from livekit.agents import (
    Agent,
    AgentSession,
    RunContext,
    function_tool,
)
from livekit.rtc import Room
from livekit.plugins import openai, deepgram, silero

# Optional: MultilingualModel for spec-mandated turn detection. The plugin may
# not be installed in every dev env; we fall back to AgentSession default if so.
try:
    from livekit.plugins.turn_detector.multilingual import MultilingualModel
    _HAS_MULTILINGUAL_TURN_DETECTOR = True
except Exception:  # pragma: no cover — depends on optional plugin
    MultilingualModel = None  # type: ignore[assignment]
    _HAS_MULTILINGUAL_TURN_DETECTOR = False

from audio_cache import get_welcome_audio_bytes, get_welcome_script
from fsm import AgentNote, InterviewState, build_interview_state
from prompts import (
    CLOSING_FALLBACK,
    build_stage_instructions,
    get_fallback_ack,
    get_transition_ack,
)
from stage_registry import (
    StageConfig,
    StageRegistry,
    StageType,
    TrackType,
    validate_agent_note,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agent-worker")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Required env (injected by worker_manager.spawn_worker)
# ---------------------------------------------------------------------------
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
DEEPGRAM_API_KEY    = os.getenv("DEEPGRAM_API_KEY")
LIVEKIT_URL         = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY     = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET  = os.getenv("LIVEKIT_API_SECRET")
INTERVIEW_ROOM_NAME = os.getenv("INTERVIEW_ROOM_NAME")

_REQUIRED = {
    "OPENAI_API_KEY":      OPENAI_API_KEY,
    "DEEPGRAM_API_KEY":    DEEPGRAM_API_KEY,
    "LIVEKIT_URL":         LIVEKIT_URL,
    "LIVEKIT_API_KEY":     LIVEKIT_API_KEY,
    "LIVEKIT_API_SECRET":  LIVEKIT_API_SECRET,
    "INTERVIEW_ROOM_NAME": INTERVIEW_ROOM_NAME,
}
_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    logger.error(f"[CONFIG] Missing required env vars: {', '.join(_missing)}")
    sys.exit(1)
logger.info(f"[CONFIG] Room: {INTERVIEW_ROOM_NAME}")


# ===========================================================================
# Eval pipeline trigger (STUBBED in Phase 1)
# ===========================================================================

async def _run_eval_pipeline(interview_id: str) -> None:
    """
    Phase 3 will replace this with the real EvalPipeline.run() call:

        from evaluator import EvalPipeline
        pipeline = EvalPipeline()
        await pipeline.run(interview_id)

    Until then we just log so the trigger wiring is exercised end-to-end.
    """
    logger.info(f"[EVAL] Would run eval pipeline for interview_id={interview_id} — stubbed in Phase 1")


# ===========================================================================
# InterviewAgent
# ===========================================================================

class InterviewAgent(Agent):
    """
    The LiveKit Agent. Uses build_stage_instructions() for every stage.
    Drives the FSM via @function_tool calls — no per-track branching here.
    """

    def __init__(
        self,
        room: Room,
        state: InterviewState,
        candidate_info: dict,
    ) -> None:
        self.room = room
        self._state_ref = state  # for use in non-RunContext code paths (on_enter)
        self.candidate_name = candidate_info.get("name", "Candidate")
        self.candidate_role = candidate_info.get("role", "this position")

        # Initial instructions = first stage (WELCOME)'s rendered template.
        first_stage = state.current_stage
        initial_instructions = build_stage_instructions(first_stage, state, self.candidate_name)
        super().__init__(instructions=initial_instructions)

    # ------------------------------------------------------------------
    # Tool: transition_stage  (with mandatory agent_note)
    # ------------------------------------------------------------------

    @function_tool
    async def transition_stage(
        self,
        ctx: RunContext[InterviewState],
        reason: Annotated[
            str,
            Field(description="Brief reason for the stage transition (e.g., 'self-intro complete', 'depth achieved')"),
        ],
        agent_note: Annotated[
            dict,
            Field(
                description=(
                    "Structured notes about what just happened. MUST match the schema "
                    "described in the current stage's [AGENT_NOTE_INSTRUCTION]. Include "
                    "every required field — schema validation runs but does not block transition."
                )
            ),
        ],
    ) -> str:
        """
        Advance the FSM to the next stage AFTER writing a stage-specific agent note.
        """
        try:
            state = ctx.userdata
            current = state.current_stage

            # --- 1. Validate the agent note against the schema -----------
            note_valid, note_errors = validate_agent_note(current.stage_type, agent_note)
            if not note_valid:
                logger.warning(
                    f"[AGENT] Agent note schema invalid for stage={current.stage_id}: "
                    f"{'; '.join(note_errors)}"
                )

            # --- 2. Persist the note (always, even if invalid) -----------
            state.stage_notes[current.stage_id] = AgentNote(
                stage_id=current.stage_id,
                stage_type=current.stage_type,
                fields=dict(agent_note),
                schema_valid=note_valid,
                schema_errors=note_errors,
                written_at=datetime.now().isoformat(),
            )
            logger.info(
                f"[AGENT] Agent note written for stage={current.stage_id} "
                f"(valid={note_valid}, fields={len(agent_note)})"
            )

            # --- 3. Resolve the next stage --------------------------------
            next_stage = state.get_next_stage()
            if not next_stage:
                return f"Cannot transition from {current.stage_id} — interview is at its final stage."

            # --- 4. Min-time gate (defensive — prevents agent racing) ----
            time_in_stage = state.time_in_current_stage()
            min_time = _min_time_in_stage(current)
            if min_time > 0 and time_in_stage < min_time:
                # Roll back the note write (we'll receive a fresh note on the next attempt).
                state.stage_notes.pop(current.stage_id, None)
                return (
                    f"Please spend more time in {current.stage_id} before transitioning. "
                    f"Elapsed {time_in_stage:.0f}s, minimum {min_time}s."
                )

            # --- 5. Advance the FSM ---------------------------------------
            logger.info(
                f"[AGENT] transition_stage from {current.stage_id} → {next_stage.stage_id} "
                f"(reason: {reason!r}, time_in_stage: {time_in_stage:.1f}s)"
            )
            state.transition_to(next_stage, forced=False, skipped=False)

            # --- 6. Update instructions for the new stage ----------------
            new_instructions = build_stage_instructions(next_stage, state, self.candidate_name)
            await self.update_instructions(new_instructions)

            # --- 7. Emit stage_change to the UI ---------------------------
            await self._emit_stage_change(next_stage)

            # --- 8. Acknowledgement handling ------------------------------
            ack = get_transition_ack(next_stage, self.candidate_name, state.job_role or "this position")
            is_closing = next_stage.stage_type == StageType.CLOSING

            if is_closing:
                state.closing_initiated = True
                return (
                    f"Stage transitioned to CLOSING. You MUST now deliver your closing remarks. "
                    f"Say: '{ack}' Do NOT ask any more questions."
                )

            if ack:
                state.pending_acknowledgement = ack
                state.pending_ack_stage = next_stage.stage_id
                logger.info(f"[AGENT] Queued transition acknowledgement for {next_stage.stage_id}")

            return (
                f"Stage transitioned to {next_stage.stage_id}. "
                f"Start your next response by acknowledging the stage change."
            )

        except Exception as e:
            logger.error(f"[AGENT] transition_stage failed: {e}", exc_info=True)
            return "I encountered an issue transitioning. Please continue with the current topic."

    # ------------------------------------------------------------------
    # Tool: ask_question  (dedup + ack injection)
    # ------------------------------------------------------------------

    @function_tool
    async def ask_question(
        self,
        ctx: RunContext[InterviewState],
        question: Annotated[str, Field(description="The exact question you want to ask")],
    ) -> str:
        """Validate and track a question before asking. Prevents repetition."""
        try:
            state = ctx.userdata
            current_stage_id = state.current_stage.stage_id
            minimum = state.current_stage.min_questions
            stage_count = state.questions_per_stage.get(current_stage_id, 0)

            # Dedup — normalize and compare against every previously-asked question
            import string
            translator = str.maketrans("", "", string.punctuation)
            normalized_new = question.lower().strip().translate(translator)
            for asked in state.questions_asked:
                normalized_asked = asked.lower().strip().translate(translator)
                if normalized_new == normalized_asked:
                    return f"DUPLICATE: This question has already been asked: '{asked}'. Ask a different question."
                if normalized_new and (normalized_new in normalized_asked or normalized_asked in normalized_new):
                    return f"SIMILAR: This question is too similar to one already asked: '{asked}'. Ask a different angle."

            # Record
            state.questions_asked.append(question)
            state.questions_per_stage[current_stage_id] = stage_count + 1
            new_count = stage_count + 1
            logger.info(
                f"[AGENT] Approved Q #{len(state.questions_asked)} "
                f"({new_count}/{minimum} in {current_stage_id})"
            )

            # Build guidance line
            t = state.get_time_status()
            time_remaining_pct = t["remaining_pct"]
            remaining_sec = t["remaining_seconds"]
            response = (
                f"Question approved ({new_count}/{minimum}). "
                f"Time: {time_remaining_pct:.0f}% ({remaining_sec:.0f}s). "
            )
            if new_count >= minimum:
                if time_remaining_pct <= 25:
                    response += "MINIMUM MET + TIME LOW. Transition soon. "
                else:
                    response += "Minimum met. May transition when ready. "
            else:
                response += f"Need {minimum - new_count} more. "
            response += f"Now ask: '{question}'"

            # Inject queued transition acknowledgement (if any)
            if state.pending_acknowledgement and not state.transition_acknowledged:
                pending_ack = state.pending_acknowledgement
                if state.pending_ack_stage == current_stage_id:
                    state.transition_acknowledged = True
                    state.pending_acknowledgement = None
                    state.pending_ack_stage = None
                response = (
                    f"STAGE TRANSITION — You MUST first say: \"{pending_ack}\" "
                    f"Then ask your question.\n\n{response}"
                )

            return response

        except Exception as e:
            logger.error(f"[AGENT] ask_question failed: {e}", exc_info=True)
            return "Error validating question. Please try again."

    # ------------------------------------------------------------------
    # Tool: assess_response
    # ------------------------------------------------------------------

    @function_tool
    async def assess_response(
        self,
        ctx: RunContext[InterviewState],
        depth_score: Annotated[
            int,
            Field(description="Response depth 1=vague, 2=surface, 3=adequate, 4=detailed, 5=comprehensive"),
        ],
        key_points_covered: Annotated[list[str], Field(description="Key points the candidate mentioned")],
    ) -> str:
        """Log an assessment of the candidate's last response and return guidance."""
        try:
            state = ctx.userdata
            assessment = {
                "stage_id": state.current_stage.stage_id,
                "depth_score": depth_score,
                "key_points": list(key_points_covered),
                "ts": time.time(),
            }
            state.assessments.append(assessment)

            q = state.get_question_status()
            t = state.get_time_status()
            status_line = (
                f"[STATUS] Q: {q['asked']}/{q['minimum']} | "
                f"Time: {t['remaining_pct']:.0f}% ({t['remaining_seconds']:.0f}s)"
            )

            if t["remaining_pct"] <= 10:
                guidance = f"{status_line}\nTIME CRITICAL: Transition NOW."
            elif q["met_minimum"] and t["remaining_pct"] <= 25:
                guidance = f"{status_line}\nMinimum met + time low. TRANSITION NOW."
            elif q["met_minimum"] and depth_score >= 3:
                guidance = f"{status_line}\nGood response + minimum met. Consider transitioning."
            elif depth_score >= 4:
                guidance = f"{status_line}\nExcellent response (depth {depth_score}/5)."
            elif depth_score <= 2 and not q["met_minimum"]:
                guidance = f"{status_line}\nBrief response. Ask follow-up for more context."
            else:
                guidance = f"{status_line}\nContinue with next question."

            # Carry pending ack through (assess_response can be called before ask_question)
            if state.pending_acknowledgement and not state.transition_acknowledged:
                guidance = (
                    f"STAGE CHANGE: First say: \"{state.pending_acknowledgement}\" Then proceed.\n\n"
                    + guidance
                )

            return guidance

        except Exception as e:
            logger.error(f"[AGENT] assess_response failed: {e}", exc_info=True)
            return "Error assessing response. Continue naturally."

    # ------------------------------------------------------------------
    # Tool: record_response
    # ------------------------------------------------------------------

    @function_tool
    async def record_response(
        self,
        ctx: RunContext[InterviewState],
        response_summary: Annotated[str, Field(description="Brief summary of candidate's key points")],
    ) -> str:
        """Record a short prose summary of the candidate's last response."""
        try:
            ctx.userdata.assessments.append({
                "stage_id": ctx.userdata.current_stage.stage_id,
                "summary": response_summary,
                "ts": time.time(),
            })
            logger.info(f"[AGENT] Recorded response summary: {response_summary[:100]}...")
            return "Response recorded. Continue naturally."
        except Exception as e:
            logger.error(f"[AGENT] record_response failed: {e}", exc_info=True)
            return "Error recording response."

    # ------------------------------------------------------------------
    # Stage-change emission to UI
    # ------------------------------------------------------------------

    async def _emit_stage_change(self, new_stage: StageConfig) -> None:
        try:
            payload = json.dumps({"type": "stage_change", "stage": new_stage.stage_id})
            if self.room and self.room.local_participant:
                await self.room.local_participant.publish_data(payload.encode("utf-8"))
                logger.info(f"[UI] Emitted stage_change: {new_stage.stage_id}")
        except Exception as e:
            logger.error(f"[UI] Failed to emit stage_change: {e}")

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def on_enter(self) -> None:
        """Play the welcome audio (cached MP3 preferred), then let the LLM speak."""
        track_value = self._state_ref.track.value
        logger.info(f"[AGENT] on_enter() for track={track_value}")

        audio_bytes = get_welcome_audio_bytes(track_value)
        if audio_bytes:
            try:
                # session.say() will use TTS internally; the cached MP3 is a
                # speed/cost optimization. If we want to play the raw bytes
                # directly that's a follow-up — for now we play the script
                # text and rely on TTS to be fast (alloy/tts-1 is ~300ms).
                await self.session.say(get_welcome_script(track_value), allow_interruptions=False)
            except Exception as e:
                logger.warning(f"[AGENT] Cached audio path failed; falling back to LLM greeting: {e}")
                self.session.generate_reply()
        else:
            logger.warning(
                f"[AGENT] No cached welcome audio for track={track_value}; using LLM greeting."
            )
            self.session.generate_reply()

    async def on_exit(self) -> None:
        logger.info("[AGENT] Agent deactivating")


# ===========================================================================
# Helpers
# ===========================================================================

def _min_time_in_stage(stage: StageConfig) -> int:
    """
    Defensive minimum time before transition is allowed. Mirrors MockFlow's
    MIN_TIMES dict, generalised by stage_type so it works for any track.
    """
    return {
        StageType.WELCOME:     0,
        StageType.SELF_INTRO:  30,
        StageType.DEPTH_STAGE: 45,
        StageType.COMPANY_FIT: 30,
        StageType.CLOSING:     0,
    }.get(stage.stage_type, 0)


async def emit_user_caption(room: Room, text: str) -> None:
    try:
        payload = json.dumps({"type": "user_caption", "text": text})
        await room.local_participant.publish_data(payload.encode("utf-8"))
    except Exception as e:
        logger.error(f"[UI] Failed to emit user caption: {e}")


async def emit_agent_caption(room: Room, text: str) -> None:
    try:
        payload = json.dumps({"type": "agent_caption", "text": text})
        await room.local_participant.publish_data(payload.encode("utf-8"))
    except Exception as e:
        logger.error(f"[UI] Failed to emit agent caption: {e}")


async def execute_skip_transition(
    session: AgentSession,
    state: InterviewState,
    target_stage: StageConfig,
    agent: InterviewAgent,
    room: Room,
) -> None:
    """Force a skip transition triggered by the frontend skip_stage data message."""
    try:
        current = state.current_stage
        logger.info(f"[SKIP] Forced skip {current.stage_id} -> {target_stage.stage_id}")
        state.transition_to(target_stage, forced=False, skipped=True)
        try:
            await agent.update_instructions(
                build_stage_instructions(target_stage, state, agent.candidate_name)
            )
        except Exception as e:
            logger.error(f"[SKIP] Failed to update instructions: {e}")
        try:
            payload = json.dumps({"type": "stage_change", "stage": target_stage.stage_id})
            await room.local_participant.publish_data(payload.encode("utf-8"))
        except Exception as e:
            logger.error(f"[SKIP] Failed to emit stage_change: {e}")

        ack = get_transition_ack(target_stage, agent.candidate_name, state.job_role or "this position")
        if ack:
            try:
                await session.say(ack, allow_interruptions=False)
            except Exception as e:
                logger.warning(f"[SKIP] session.say failed: {e}")
    except Exception as e:
        logger.error(f"[SKIP] execute_skip_transition error: {e}", exc_info=True)


# ===========================================================================
# Fallback timer
# ===========================================================================

async def stage_fallback_timer(
    session: AgentSession,
    state: InterviewState,
    room: Room,
    agent: InterviewAgent,
    interview_complete: asyncio.Event,
) -> None:
    """Monitor per-stage time. Force transition if a stage exceeds its limit."""
    CLOSING_TIMEOUT = 60
    logged_milestones: set[int] = set()
    last_logged_stage: Optional[str] = None
    closing_timeout_logged = False

    logger.info("[TIMER] Fallback timer started")

    try:
        while not interview_complete.is_set():
            await asyncio.sleep(5)
            if interview_complete.is_set():
                break

            current = state.current_stage

            # ---- CLOSING stage handling (separate path) -----------------
            if current.stage_type == StageType.CLOSING:
                elapsed = state.time_in_current_stage()
                if not closing_timeout_logged:
                    logger.info(f"[TIMER] Closing stage timeout: {CLOSING_TIMEOUT}s")
                    closing_timeout_logged = True
                if elapsed > CLOSING_TIMEOUT and not state.closing_message_delivered:
                    logger.warning("[FALLBACK] Closing timeout — forcing finalization")
                    try:
                        msg = CLOSING_FALLBACK.message.replace("[CANDIDATE_NAME]", agent.candidate_name)
                        await session.say(msg, allow_interruptions=False)
                        await asyncio.sleep(3.0)
                    except Exception as e:
                        logger.warning(f"[FALLBACK] Closing say failed: {e}")
                    interview_complete.set()
                    try:
                        await room.disconnect()
                    except Exception:
                        pass
                    break
                continue

            # ---- WELCOME stage is not monitored (transitioned immediately by LLM)
            if current.stage_type == StageType.WELCOME:
                if last_logged_stage != current.stage_id:
                    last_logged_stage = current.stage_id
                    logged_milestones = set()
                continue

            limit = current.time_limit
            elapsed = state.time_in_current_stage()
            elapsed_pct = min(100.0, (elapsed / limit) * 100) if limit > 0 else 0

            if current.stage_id != last_logged_stage:
                logger.info(f"[TIMER] Stage '{current.stage_id}' — limit {limit}s")
                logged_milestones = set()
                last_logged_stage = current.stage_id

            for pct in (50, 75, 90, 100):
                if elapsed_pct >= pct and pct not in logged_milestones:
                    logger.info(f"[TIMER] {current.stage_id} at {pct}% ({elapsed:.0f}/{limit}s)")
                    logged_milestones.add(pct)

            if elapsed > limit:
                next_stage = state.get_next_stage()
                if not next_stage:
                    continue
                logger.warning(
                    f"[FALLBACK] FORCING transition: {current.stage_id} -> {next_stage.stage_id}"
                )
                state.transition_to(next_stage, forced=True)
                try:
                    await agent.update_instructions(
                        build_stage_instructions(next_stage, state, agent.candidate_name)
                    )
                except Exception as e:
                    logger.error(f"[FALLBACK] Instruction update error: {e}")
                try:
                    payload = json.dumps({"type": "stage_change", "stage": next_stage.stage_id})
                    await room.local_participant.publish_data(payload.encode("utf-8"))
                except Exception as e:
                    logger.error(f"[UI] stage_change emit error: {e}")

                ack = get_fallback_ack(next_stage, agent.candidate_name)
                if ack:
                    state.pending_acknowledgement = ack
                    state.pending_ack_stage = next_stage.stage_id
                    try:
                        await session.say(ack)
                    except Exception as e:
                        logger.warning(f"[FALLBACK] session.say failed: {e}")

                logged_milestones = set()
                last_logged_stage = next_stage.stage_id

    except asyncio.CancelledError:
        logger.info("[TIMER] Fallback timer cancelled")
    except Exception as e:
        logger.error(f"[TIMER] Error: {e}", exc_info=True)


# ===========================================================================
# Main entry — runs the whole session
# ===========================================================================

async def run_interview() -> None:
    """
    Entrypoint. Connects directly to room (no LiveKit dispatch). Waits for the
    participant, builds InterviewState from the participant's attributes, then
    runs the AgentSession until the interview completes.
    """
    logger.info(f"[MAIN] Starting interview agent for room: {INTERVIEW_ROOM_NAME}")

    interview_complete = asyncio.Event()
    fallback_task: Optional[asyncio.Task] = None
    http_session: Optional[aiohttp.ClientSession] = None
    room: Optional[Room] = None

    try:
        # Shared HTTP session for plugins (required when not using cli.run_app)
        http_session = aiohttp.ClientSession()
        logger.info("[MAIN] Created shared HTTP session")

        # ---- Agent token ----
        token = livekit_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        token.with_identity("interview-agent")
        token.with_name("AI Interviewer")
        token.with_grants(livekit_api.VideoGrants(
            room_join=True,
            room=INTERVIEW_ROOM_NAME,
            can_publish=True,
            can_subscribe=True,
        ))
        agent_token = token.to_jwt()
        logger.info(f"[MAIN] Agent token generated for room {INTERVIEW_ROOM_NAME}")

        # ---- Connect to room ----
        room = Room()
        logger.info(f"[MAIN] Connecting to LiveKit: {LIVEKIT_URL}")
        await room.connect(LIVEKIT_URL, agent_token)
        logger.info(f"[MAIN] Connected to room: {room.name}")

        # ---- Wait for the participant ----
        room_parts = room.name.split("-")
        candidate_name = " ".join(room_parts[1:-1]).title() if len(room_parts) > 2 else "Candidate"

        wait_start = asyncio.get_event_loop().time()
        while not room.remote_participants:
            if asyncio.get_event_loop().time() - wait_start > 60:
                logger.error("[MAIN] Timed out waiting for participant")
                await room.disconnect()
                return
            await asyncio.sleep(0.5)

        participant = list(room.remote_participants.values())[0]
        attrs: dict = dict(getattr(participant, "attributes", {}) or {})

        role            = attrs.get("role", "this position")
        level           = attrs.get("level", "mid")
        email           = attrs.get("email", "")
        resume_text     = attrs.get("resume_text") or None
        job_description = attrs.get("job_description") or None
        company_name    = attrs.get("company_name") or None
        include_profile = attrs.get("include_profile", "true").lower() == "true"
        user_id         = attrs.get("user_id") or None
        track_str       = attrs.get("track", "intro")

        # Resolve TrackType (fall back to INTRO if frontend sends something we
        # don't yet implement — Phase 1 disables non-intro buttons anyway).
        try:
            track = TrackType(track_str)
        except ValueError:
            logger.warning(f"[MAIN] Unknown track '{track_str}', defaulting to INTRO")
            track = TrackType.INTRO

        if not StageRegistry.is_implemented(track):
            logger.warning(
                f"[MAIN] Track '{track.value}' has no stage sequence yet (Phase 1 ships only INTRO). "
                f"Falling back to INTRO."
            )
            track = TrackType.INTRO

        logger.info(
            f"[MAIN] Participant attrs — track={track.value}, role={role}, level={level}, "
            f"has_resume={bool(resume_text)}, has_jd={bool(job_description)}"
        )

        # ---- Build interview state ----
        state = build_interview_state(track)
        state.candidate_name      = candidate_name
        state.candidate_email     = email
        state.job_role            = role
        state.experience_level    = level
        state.uploaded_resume_text = resume_text
        state.job_description     = job_description
        state.include_profile     = include_profile
        state.company_name        = company_name
        # Anchor the WELCOME stage's start timestamp (no transition — we're at index 0).
        state.stage_started_at = datetime.now()
        state.last_state_verification = datetime.now()

        candidate_info = {"name": candidate_name, "role": role}
        logger.info(f"[MAIN] Candidate: {candidate_name} (role={role}, level={level})")

        # ---- Plugins (spec-mandated configs) ----
        try:
            stt = deepgram.STT(
                model="nova-2",
                language="en-US",
                smart_format=True,
                http_session=http_session,
            )
            logger.info("[MAIN] Deepgram STT initialised")
        except Exception as e:
            logger.error(f"[MAIN] Deepgram STT init failed: {e}", exc_info=True)
            raise

        try:
            llm = openai.LLM(model="gpt-4o-mini", temperature=0.7)
            logger.info("[MAIN] OpenAI LLM (gpt-4o-mini) initialised")
        except Exception as e:
            logger.error(f"[MAIN] OpenAI LLM init failed: {e}", exc_info=True)
            raise

        try:
            tts = openai.TTS(model="tts-1", voice="alloy", speed=1.0)
            logger.info("[MAIN] OpenAI TTS (tts-1 alloy) initialised")
        except Exception as e:
            logger.error(f"[MAIN] OpenAI TTS init failed: {e}", exc_info=True)
            raise

        try:
            vad = silero.VAD.load(
                min_speech_duration=0.1,
                min_silence_duration=0.3,
                padding_duration=0.1,
                max_buffered_speech=30.0,
                activation_threshold=0.5,
                sample_rate=16000,
            )
            logger.info("[MAIN] Silero VAD initialised")
        except Exception as e:
            logger.error(f"[MAIN] Silero VAD init failed: {e}", exc_info=True)
            raise

        # ---- Agent + Session ----
        agent = InterviewAgent(room=room, state=state, candidate_info=candidate_info)
        logger.info(f"[MAIN] InterviewAgent created for {candidate_name}")

        session_kwargs: dict[str, Any] = dict(
            userdata=state,
            stt=stt,
            llm=llm,
            tts=tts,
            vad=vad,
            allow_interruptions=True,
            min_endpointing_delay=0.5,      # SPEC — do not change without authority
            max_endpointing_delay=3.0,      # SPEC
            min_interruption_duration=0.5,
            discard_audio_if_uninterruptible=True,
        )
        if _HAS_MULTILINGUAL_TURN_DETECTOR and MultilingualModel is not None:
            session_kwargs["turn_detection"] = MultilingualModel()
            logger.info("[MAIN] Turn detection: MultilingualModel")
        else:
            logger.warning(
                "[MAIN] livekit-plugins-turn-detector unavailable — using default turn detection. "
                "Run `pip install livekit-plugins-turn-detector` for the spec config."
            )

        session = AgentSession(**session_kwargs)
        logger.info("[MAIN] AgentSession created")

        # ---- Conversation history collection ----
        conversation_history: dict[str, list[dict]] = {"agent": [], "user": []}
        closing_finalized = {"done": False}

        @session.on("user_input_transcribed")
        def on_user_speech(event):
            if not event.is_final:
                return
            transcript = (event.transcript or "").strip()
            if not transcript:
                return
            logger.info(f"[USER] {transcript}")
            conversation_history["user"].append({
                "index": len(conversation_history["user"]),
                "text": transcript,
                "timestamp": time.time(),
            })
            if room:
                asyncio.create_task(emit_user_caption(room, transcript))

        @session.on("conversation_item_added")
        def on_conversation_item(event):
            try:
                message = event.item
                if not (hasattr(message, "role") and message.role == "assistant"):
                    return
                agent_text = message.text_content if hasattr(message, "text_content") else None
                if not agent_text:
                    return
                logger.info(f"[AGENT] {agent_text[:150]}...")
                conversation_history["agent"].append({
                    "index": len(conversation_history["agent"]),
                    "text": agent_text,
                    "timestamp": time.time(),
                    "stage": state.current_stage.stage_id,
                })
                if room:
                    asyncio.create_task(emit_agent_caption(room, agent_text))

                # CLOSING content detection — natural goodbye triggers 5s-wait disconnect
                if state.current_stage.stage_type == StageType.CLOSING and not closing_finalized["done"]:
                    text_lower = agent_text.lower()
                    indicators = [
                        ("thank you" in text_lower) and ("luck" in text_lower),
                        "good luck" in text_lower,
                        "best of luck" in text_lower,
                        "great speaking" in text_lower,
                        "take care" in text_lower,
                    ]
                    if any(indicators) and len(agent_text) > 50:
                        state.closing_message_delivered = True
                        async def schedule_finalization():
                            if closing_finalized["done"]:
                                return
                            closing_finalized["done"] = True
                            await asyncio.sleep(5.0)
                            await finalize_and_disconnect()
                        asyncio.create_task(schedule_finalization())
            except Exception as e:
                logger.error(f"[CONVERSATION] Error: {e}", exc_info=True)

        @room.on("data_received")
        def on_data_received(data_packet):
            try:
                payload = json.loads(data_packet.data.decode("utf-8"))
                ptype = payload.get("type")

                if ptype == "skip_stage":
                    target_id = payload.get("target_stage")
                    target = state.get_stage_by_id(target_id) if target_id else None
                    if target and state.can_skip_to(target_id):
                        logger.info(f"[SKIP] Skip request to {target_id}")
                        asyncio.create_task(
                            execute_skip_transition(session, state, target, agent, room)
                        )
                    else:
                        logger.warning(f"[SKIP] Invalid or unreachable target: {target_id}")
                # Other data-channel events are added in later phases.
            except Exception as e:
                logger.error(f"[DATA] Error processing data: {e}", exc_info=True)

        # ---- Finalization ----
        async def finalize_and_disconnect():
            try:
                if not user_id:
                    logger.error("[FINALIZE] No user_id — skipping DB save")
                    if room:
                        await room.disconnect()
                    return

                now = datetime.now()
                interview_data = {
                    "candidate_name":    state.candidate_name,
                    "interview_date":    now.isoformat(),
                    "room_name":         room.name if room else "",
                    "job_role":          state.job_role,
                    "experience_level":  state.experience_level,
                    "company_name":      state.company_name,
                    "conversation":      conversation_history,
                    "total_messages": {
                        "agent": len(conversation_history["agent"]),
                        "user":  len(conversation_history["user"]),
                    },
                    "skipped_stages":   state.skipped_stages,
                    "final_stage":      state.current_stage.stage_id,
                    "ended_by":         "natural_completion",
                    "has_resume":       bool(state.uploaded_resume_text),
                    "has_jd":           bool(state.job_description),
                    "track":            state.track.value,
                    "track_config":     {},
                    "stage_notes":      state.stage_notes_to_dict(),
                }

                from supabase_client import supabase_client  # local import to keep startup fast
                interview_id = supabase_client.save_interview(user_id, interview_data)

                if interview_id:
                    state._interview_id = interview_id
                    state._user_id = user_id
                    logger.info(f"[FINALIZE] Interview saved: {interview_id}")
                    try:
                        await room.local_participant.publish_data(
                            json.dumps({
                                "type": "interview_saved",
                                "interview_id": interview_id,
                                "message": "Interview saved successfully",
                            }).encode("utf-8")
                        )
                        await room.local_participant.publish_data(
                            json.dumps({"type": "interview_ending", "message": "Interview Complete"}).encode("utf-8")
                        )
                    except Exception as e:
                        logger.warning(f"[FINALIZE] Failed to emit interview_saved/ending: {e}")

                    # ---- Fire-and-forget eval trigger (STUBBED in Phase 1) ----
                    try:
                        asyncio.create_task(_run_eval_pipeline(interview_id))
                    except Exception as e:
                        logger.error(f"[EVAL] Failed to start eval pipeline: {e}", exc_info=True)
                else:
                    logger.error("[FINALIZE] DB save failed")
                    try:
                        await room.local_participant.publish_data(
                            json.dumps({"type": "save_error", "message": "Failed to save interview."}).encode("utf-8")
                        )
                    except Exception:
                        pass

                closing_finalized["done"] = True
                interview_complete.set()
                await asyncio.sleep(2.0)
                if room:
                    await room.disconnect()
                logger.info("[FINALIZE] Disconnected")

            except Exception as e:
                logger.error(f"[FINALIZE] Error: {e}", exc_info=True)
                try:
                    if room:
                        await room.disconnect()
                except Exception:
                    pass
                interview_complete.set()

        async def save_transcript_on_disconnect():
            """Save fallback if the room disconnects without a graceful close."""
            try:
                if closing_finalized.get("done"):
                    return
                if not conversation_history["agent"] and not conversation_history["user"]:
                    return
                if not user_id:
                    return

                now = datetime.now()
                interview_data = {
                    "candidate_name":    state.candidate_name,
                    "interview_date":    now.isoformat(),
                    "room_name":         room.name if room else "",
                    "job_role":          state.job_role,
                    "experience_level":  state.experience_level,
                    "company_name":      state.company_name,
                    "conversation":      conversation_history,
                    "total_messages": {
                        "agent": len(conversation_history["agent"]),
                        "user":  len(conversation_history["user"]),
                    },
                    "skipped_stages":   state.skipped_stages,
                    "final_stage":      state.current_stage.stage_id,
                    "ended_by":         "user_disconnect",
                    "has_resume":       bool(state.uploaded_resume_text),
                    "has_jd":           bool(state.job_description),
                    "track":            state.track.value,
                    "track_config":     {},
                    "stage_notes":      state.stage_notes_to_dict(),
                }

                from supabase_client import supabase_client
                interview_id = supabase_client.save_interview(user_id, interview_data)
                if interview_id:
                    state._interview_id = interview_id
                    state._user_id = user_id
                    logger.info(f"[HISTORY] Saved transcript on disconnect: {interview_id}")
                    try:
                        await room.local_participant.publish_data(
                            json.dumps({"type": "interview_saved", "interview_id": interview_id}).encode("utf-8")
                        )
                    except Exception:
                        pass
                    # Trigger eval here too — same fire-and-forget contract.
                    try:
                        asyncio.create_task(_run_eval_pipeline(interview_id))
                    except Exception as e:
                        logger.error(f"[EVAL] Failed to start eval pipeline (disconnect path): {e}",
                                     exc_info=True)
            except Exception as e:
                logger.error(f"[HISTORY] save_transcript_on_disconnect error: {e}", exc_info=True)

        @room.on("disconnected")
        def on_room_disconnected():
            logger.info("[ROOM] Room disconnected")
            asyncio.create_task(save_transcript_on_disconnect())
            interview_complete.set()

        # ---- Fallback timer task ----
        fallback_task = asyncio.create_task(
            stage_fallback_timer(session, state, room, agent, interview_complete)
        )

        # ---- Run the session ----
        logger.info("[MAIN] Starting agent session...")
        await session.start(agent=agent, room=room)
        logger.info("[MAIN] Agent session started — awaiting completion...")

        await interview_complete.wait()
        logger.info("[MAIN] Interview complete")

    except asyncio.CancelledError:
        logger.info("[MAIN] Interview cancelled")
    except Exception as e:
        logger.error(f"[MAIN] Error: {e}", exc_info=True)
    finally:
        logger.info("[MAIN] Starting cleanup...")
        if fallback_task and not fallback_task.done():
            fallback_task.cancel()
            try:
                await fallback_task
            except asyncio.CancelledError:
                pass

        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

        if room:
            try:
                logger.info("[MAIN] Disconnecting from room...")
                await room.disconnect()
            except Exception as e:
                logger.warning(f"[MAIN] Room disconnect error: {e}")

        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

        if http_session:
            try:
                await http_session.close()
            except Exception as e:
                logger.warning(f"[MAIN] HTTP session close error: {e}")

        logger.info("[MAIN] Cleanup complete, exiting")
        sys.exit(0)


if __name__ == "__main__":
    logger.info("[WORKER] Starting agent worker — DIRECT ROOM CONNECTION MODE")
    asyncio.run(run_interview())

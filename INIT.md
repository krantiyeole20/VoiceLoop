# VoiceLoop — INIT.md (Build Bible)

> Status: Build plan. No implementation code lives in this repo yet.
> Reference implementation: `../external-stuff/MockFlow-AI/`
> Source planning docs: `VOICELOOP_FEATURE_MAP_V2.md`, `EVALUATOR_LAYER1.md`
> Repository root for VoiceLoop code: `/Users/krantiy/Documents/VoiceLoop/VoiceLoop/`

This document is the single source of truth for what will be built. A different engineer should be able to pick up any phase below and implement it without asking questions. Every file, every dataclass, every schema column, every design trade-off is documented here.

Implementation is gated. Phases must be completed in order. Each phase is independently runnable. The human approves before the next phase starts.

---

## 1. Project Identity

- **Name:** VoiceLoop
- **Tagline:** Real-time voice AI mock interview platform with a post-session stage-bifurcated self-evaluator loop.
- **Core differentiator vs. reference (MockFlow-AI):**
  1. **Stage Collapse** — 12 reusable `StageType` archetypes + `StageConfig` instances + a generic FSM, replacing MockFlow's per-track enums/state classes.
  2. **Six tracks** instead of MockFlow's four (adds Data Science/ML, Analytics/BI, Product/Strategy).
  3. **Agent Note** — at every stage transition, the agent writes a structured note revealing its intent.
  4. **Post-session two-face evaluator** — runs once per session, evaluates every stage with both an agent-conduct face and a candidate-performance face. LLM-as-judge over a frozen rubric. Progressive disclosure across stages.
  5. **MCP server** exposing the evaluator for external consumption.
  6. **Aggregator + prompt store** — manual, human-gated patch proposals after N sessions; reversible.

Everything else (voice infra, BYOK, auth, persistence pattern, speech analytics, document upload, candidate feedback) carries over from MockFlow with minimal change.

---

## 2. Complete File Tree

```
VoiceLoop/
├── INIT.md                              # This file
├── VOICELOOP_FEATURE_MAP_V2.md          # Source feature spec (already in repo)
├── EVALUATOR_LAYER1.md                  # Source eval spec (already in repo)
├── README.md                            # Setup, run, dev notes
├── LICENSE
├── .gitignore
├── .gitattributes
├── env.template                         # Copy to .env, fill in keys
├── requirements.txt                     # Python deps (pinned)
├── supabase_schema.sql                  # All DDL + RLS in one file
├── generate_welcome_audio.py            # One-shot script (run per track)
├── verify_setup.py                      # Smoke test (db conn, env vars)
│
│   ── CORE PYTHON MODULES ──────────────────────────────────────────
├── app.py                               # Flask server (auth, /api/token, /api/eval-report, /api/feedback, /api/upload-resume, /api/extract-topics, /api/interviews, /api/skip-stage)  [ADAPTED]
├── agent_worker.py                      # LiveKit AgentSession subprocess. Generic FSM-driven. Writes agent notes. Fires eval trigger.  [ADAPTED]
├── worker_manager.py                    # Spawns/terminates agent_worker subprocesses with BYOK env. 8s Silero warmup wait.  [VERBATIM]
├── fsm.py                               # Generic InterviewState. Iterates StageConfig list. No per-track subclasses.  [ADAPTED — restructured]
├── stage_registry.py                    # StageType enum, StageConfig dataclass, TrackType enum, StageRegistry. All six track sequences.  [NEW]
├── prompts.py                           # Template-based per StageType. AGENT_NOTE_PROMPTS. POSTINTERVIEWFEEDBACK, FEEDBACKSCORES, ROLE_CONTEXT, PERSONALITY, QUESTION_GENERATION carry over.  [ADAPTED]
├── supabase_client.py                   # User/keys/interview/feedback CRUD. Adds stage_notes column. Adds eval_report + stage_eval_reports methods.  [ADAPTED]
├── auth_helpers.py                      # require_auth, get_current_user, get_user_id.  [VERBATIM]
├── document_processor.py                # PDF/DOCX/MD/TXT text extraction, MD5 in-memory cache.  [VERBATIM]
├── audio_cache.py                       # Pre-generated welcome MP3 per track. Extended map for six tracks.  [VERBATIM — minor map extension]
├── speech_analytics.py                  # Filler counting, WPM, per-turn pace. Pure functions.  [VERBATIM]
├── conversation_cache.py                # In-memory conversation cache (room_name + ts → dict).  [VERBATIM]
├── postprocess.py                       # merge_by_agent_turns, list_interviews, get_interview_summary. (Used by app.py.)  [VERBATIM]
│
│   ── EVAL LAYER (NEW — Phase 3+) ────────────────────────────────────
├── evaluator.py                         # EvalPipeline, EvalAgent, _run_eval_pipeline, all eval dataclasses  [NEW]
├── eval_rubrics.py                      # One rubric per StageType (12). Dimension names, score guidance, calibration examples.  [NEW]
├── aggregator.py                        # Reads eval_patches/*.json. Groups failures. Outputs AggregationReport with proposed PromptPatch.  [NEW]
├── prompt_store.py                      # Versioned prompt management. apply_patch, rollback, version log.  [NEW]
├── eval_mcp_server.py                   # MCP server: evaluate_stage, evaluate_session, get_open_threads, compare_sessions, get_agent_performance_trend  [NEW]
│
│   ── TEMPLATES ────────────────────────────────────────────────────
├── templates/
│   ├── index.html                       # Landing page  [VERBATIM]
│   ├── auth_callback.html               # OAuth token extraction  [VERBATIM]
│   ├── dashboard.html                   # User dashboard  [VERBATIM]
│   ├── api_keys.html                    # BYOK form  [VERBATIM]
│   ├── form.html                        # Candidate registration + track picker. UPDATED with six tracks.  [ADAPTED]
│   ├── interview.html                   # In-room UI, LiveKit client, captions, stage display  [VERBATIM]
│   ├── past_calls.html                  # Interview history list  [VERBATIM]
│   ├── feedback.html                    # Candidate-facing feedback viewer  [VERBATIM]
│   ├── eval_report.html                 # Two-face evaluator report viewer (per-stage breakdown, programmatic charts)  [NEW]
│   └── error.html                       # 404/500 pages  [VERBATIM]
│
│   ── STATIC ASSETS ────────────────────────────────────────────────
├── static/
│   ├── audio/
│   │   ├── welcome_intro.mp3            # Pre-generated TTS  [GEN]
│   │   ├── welcome_behavioral.mp3       # Pre-generated TTS  [GEN]
│   │   ├── welcome_technical_swe.mp3    # Pre-generated TTS  [GEN]
│   │   ├── welcome_ds_ml.mp3            # Pre-generated TTS  [GEN]
│   │   ├── welcome_analytics.mp3        # Pre-generated TTS  [GEN]
│   │   └── welcome_product.mp3          # Pre-generated TTS  [GEN]
│   ├── styles.css, form.css, interview.css, feedback.css, past_calls.css, modals.css  [VERBATIM]
│   └── script.js, auth.js, header.js, modal.js, dashboard.js, apikeys.js  [VERBATIM]
│
├── public/
│   └── favicon.ico                      [VERBATIM]
│
│   ── EVAL ARTIFACTS (runtime-generated, gitignored) ─────────────────
├── eval_patches/                        # SessionEvalReport JSON per session
│   └── {session_id}.json
├── prompt_versions/                     # Versioned prompt snapshots
│   ├── v1.json
│   └── v2.json
└── logs/                                # Optional file logs
```

**Files removed vs. MockFlow:**
- `tracks/` directory (replaced by `stage_registry.py`)
- `agent.py` (MockFlow had a top-level agent file unused after worker split — never copy this in)
- `Patch1.md`, `UPDATE-PATCH.md`, `patch2.md`, `add_livekit_keys_migration.sql` (planning artifacts, not relevant)
- Coding-track code (`tracks/technical_coding.py`, code editor templates, `coding_submissions` table) — VoiceLoop V1 does not include coding. Out of scope for all six tracks.

---

## 3. File-by-File Responsibilities

### 3.1 Adapted from MockFlow (read reference, do not copy verbatim)

#### `fsm.py` — Generic FSM
**Responsibility:** Hold the live `InterviewState` for one session. Track current `StageConfig`, time in stage, transition history, queued acknowledgements, pending transitions, candidate metadata, document text, and the **`stage_notes`** dict (new — `dict[stage_id → AgentNote]`).

**Removed vs. MockFlow:**
- `InterviewStage`, `BehavioralStage`, `TechnicalVoiceStage`, `CodingStage` enums — gone.
- `STAGE_TIME_LIMITS`, `BEHAVIORAL_STAGE_TIME_LIMITS`, etc. dicts — gone (values live in `StageConfig` instances).
- `BehavioralInterviewState`, `TechnicalVoiceInterviewState`, `CodingInterviewState` subclasses — gone.
- All `get_next_<track>_stage()` methods — gone.

**Public interface:**
```python
@dataclass
class InterviewState:
    # Sequence reference
    track: TrackType
    stage_sequence: list[StageConfig]      # injected at session start
    current_index: int = 0                 # index into stage_sequence
    # Candidate
    candidate_name: str = ""
    candidate_email: str = ""
    job_role: str = ""
    experience_level: str = ""
    company_name: str | None = None
    company_context: str | None = None
    # Document context
    uploaded_resume_text: str | None = None
    job_description: str | None = None
    include_profile: bool = True
    # Stage tracking
    stage_started_at: datetime | None = None
    last_state_verification: datetime | None = None
    questions_asked: list[str] = field(default_factory=list)
    questions_per_stage: dict[str, int] = field(default_factory=dict)
    transition_count: int = 0
    forced_transitions: int = 0
    skipped_stages: list[str] = field(default_factory=list)
    # Transition acknowledgement (queued mid-speech)
    pending_acknowledgement: str | None = None
    pending_ack_stage: str | None = None
    transition_acknowledged: bool = False
    # Closing safety
    closing_initiated: bool = False
    closing_message_delivered: bool = False
    # NEW — agent notes per stage (load-bearing for eval)
    stage_notes: dict[str, AgentNote] = field(default_factory=dict)
    # Eval bookkeeping (set on save)
    _user_id: str | None = None
    _interview_id: str | None = None

    @property
    def current_stage(self) -> StageConfig: ...
    def get_next_stage(self) -> StageConfig | None: ...
    def transition_to(self, target: StageConfig, forced=False, skipped=False) -> None: ...
    def can_skip_to(self, target_stage_id: str) -> bool: ...
    def get_stage_by_id(self, stage_id: str) -> StageConfig | None: ...
    def time_in_current_stage(self) -> float: ...
    def get_time_status(self) -> dict: ...           # elapsed, limit, remaining_pct, etc.
    def get_question_status(self) -> dict: ...       # asked, minimum, met_minimum
    def get_progress_summary(self) -> str: ...
    def should_transition_soon(self) -> bool: ...
    def get_document_context(self) -> str: ...       # delegates to StageConfig.params['document_injection']
    def to_dict(self) -> dict: ...                   # for logging
```

`StageConfig` itself lives in `stage_registry.py`. The FSM imports it.

---

#### `agent_worker.py` — LiveKit AgentSession subprocess
**Responsibility:** One subprocess per interview session. Connects directly to LiveKit room with BYOK keys from env. Hosts the `InterviewAgent` (`livekit.agents.Agent` subclass). Drives the FSM via `@function_tool` calls. Writes the agent note before each transition. Saves conversation + `stage_notes` to Supabase at end-of-session. Fires the eval pipeline as a fire-and-forget background task.

**Key class:**
```python
class InterviewAgent(Agent):
    def __init__(self, room, candidate_info, stage_sequence, track):
        # stage_sequence comes from StageRegistry; first StageConfig is WELCOME
        # Initial instructions = build_stage_instructions(stage_sequence[0], params, candidate_info)
        ...

    @function_tool
    async def transition_stage(self, ctx, reason: str,
                                agent_note: dict) -> str:
        """
        agent_note must conform to ctx.userdata.current_stage.agent_note_schema.
        - Validate schema; on failure, log + flag but still allow transition.
        - Persist into ctx.userdata.stage_notes[current_stage.stage_id].
        - Advance FSM via get_next_stage().
        - Update agent.instructions to next stage's template.
        - Emit stage_change data channel event.
        - Queue acknowledgement (or speak it immediately for CLOSING).
        Returns guidance string for LLM.
        """

    @function_tool
    async def ask_question(self, ctx, question: str) -> str:
        """Dedup against questions_asked. Append to questions_per_stage[stage_id].
           If pending_acknowledgement present and matches current stage,
           prepend 'STAGE TRANSITION — You MUST first say: ...' and clear flag."""

    @function_tool
    async def assess_response(self, ctx, depth_score: int,
                              key_points_covered: list[str]) -> str:
        """Log assessment to FSM history for later eval as programmatic signal.
           Returns brief guidance + transition-pressure hint."""

    @function_tool
    async def record_response(self, ctx, response_summary: str) -> str: ...

    @function_tool
    async def generate_interview_questions(self, ctx, count: int) -> str:
        """Reads current stage_sequence + StageConfig.params to dispatch
           the right QUESTION_GENERATION prompt. Returns count, populates
           ctx.userdata generated questions per stage type."""

    @function_tool
    async def get_current_question(self, ctx) -> str: ...

    async def on_enter(self):
        """Play welcome audio (cached MP3 if available, fallback to live TTS).
           Then session.generate_reply() to let LLM produce greeting."""

    async def on_exit(self): ...
```

**Top-level functions:**
- `run_interview()` — main entrypoint (called from `if __name__ == "__main__"`). Generates agent JWT, connects room, waits for participant, parses attributes, builds `InterviewState`, instantiates plugins, starts `AgentSession`, runs fallback timer, awaits `interview_complete`, then cleans up.
- `stage_fallback_timer(...)` — async task. Polls every 5s. Logs milestones at 50/75/90/100%. Forces transition if `time_in_current_stage()` exceeds `current_stage.time_limit`. Handles CLOSING separately with `CLOSING_TIMEOUT=60s`. Catches `asyncio.CancelledError`.
- `execute_skip_transition(...)` — forced skip from data channel `skip_stage` message.
- `finalize_and_disconnect()` — saves interview + stage_notes to Supabase, fires `asyncio.create_task(_run_eval_pipeline(session_id))`, disconnects after 2s.
- `save_transcript_on_disconnect()` — fallback save on abrupt disconnect.
- `emit_user_caption(...)`, `emit_agent_caption(...)` — data channel emit helpers.

**Critical: AgentSession config (carry-over verbatim from spec):**
```python
session = AgentSession(
    userdata=interview_state,
    stt=deepgram.STT(model="nova-2", language="en-US", smart_format=True),
    llm=openai.LLM(model="gpt-4o-mini", temperature=0.7),
    tts=openai.TTS(model="tts-1", voice="alloy"),
    vad=silero.VAD.load(...),                  # MockFlow's tuned params
    turn_detection=MultilingualModel(),
    allow_interruptions=True,
    min_endpointing_delay=0.5,                 # Per spec — NOT 0.8 from MockFlow's CPU-tuned worker
    max_endpointing_delay=3.0,                 # Per spec — NOT 4.0
    min_interruption_duration=0.5,
    discard_audio_if_uninterruptible=True,
)
```

> **Note on the 0.5/3.0 vs. MockFlow's 0.8/4.0:** The user-supplied spec explicitly mandates 0.5/3.0 as the empirically correct interview-pacing values; MockFlow's worker had been slowed down for Render's CPU. VoiceLoop is not assumed to run on Render, so we use the spec values. If we later deploy to a constrained CPU and hear "responses feel slow," we can bump them — but only as a deployment-time toggle, never as a coding default.

---

#### `prompts.py` — Template-based prompt registry
**Responsibility:** Per `StageType` template classes. Each class has `conversation`, `style`, `focus_areas`, `rules`, `transition` attributes containing strings with `{placeholders}` filled at runtime from `StageConfig.params`.

**Structure (one class per StageType — 12 total):**
```python
class WELCOME_TEMPLATE: ...        # uses {track_name}, {tone}, {framework_hint}
class SELF_INTRO_TEMPLATE: ...     # uses {depth_expectation}, {focus_hint}
class DEPTH_STAGE_TEMPLATE: ...    # uses {focus_area}
class COMPANY_FIT_TEMPLATE: ...
class BEHAVIORAL_Q_TEMPLATE: ...   # uses {competency}, {framework}, {depth_setting}, {question_index}, {total_questions}, {question_text}
class TECHNICAL_CONCEPTS_TEMPLATE: ...  # uses {domain}, {topic_name}, {experience_level}
class SYSTEM_DESIGN_TEMPLATE: ...  # uses {variant}  ("software"|"ml_pipeline")
class SQL_PROBLEM_TEMPLATE: ...    # uses {problem_prompt}
class BUSINESS_CASE_TEMPLATE: ...  # uses {case_type}  ("analytics"|"strategy")
class PRODUCT_SENSE_TEMPLATE: ...
class ANALYTICAL_METRICS_TEMPLATE: ...
class CLOSING_TEMPLATE: ...        # uses {track_name}, {follow_up_allowed}
```

**Other classes (carry over content from MockFlow with minimal change):**
- `TRANSITION_ACKS` — keyed on `stage_type`, NOT on hardcoded stage names. Phrases use `{candidate_name}`, `{job_role}`, `{next_focus}`.
- `FALLBACK_ACKS` — same keying.
- `AGENT_NOTE_PROMPTS` — new section. One string per `StageType` describing exactly what the agent must include in `agent_note` for that stage. Injected into the stage instructions so the LLM knows the contract.
- `ROLE_CONTEXT` — verbatim from MockFlow (role_keywords, level_expectations, template).
- `PERSONALITY` — verbatim.
- `POSTINTERVIEWFEEDBACK` — verbatim. Generates candidate-facing markdown feedback.
- `FEEDBACKSCORES` — verbatim. Generates competency JSON.
- `QUESTION_GENERATION` — verbatim (behavioral_system, technical_system, topic_extraction_system; behavioral_framework_competencies dict). Add new keys for SYSTEM_DESIGN, SQL_PROBLEM, BUSINESS_CASE, PRODUCT_SENSE, ANALYTICAL_METRICS.
- `CLOSING_FALLBACK.message` — verbatim.

**Helper functions:**
```python
def build_stage_instructions(stage_config: StageConfig,
                              state: InterviewState,
                              candidate_name: str) -> str:
    """Render template for stage_type with params + document context + role/personality."""

def get_transition_ack(next_stage: StageConfig,
                        candidate_name: str,
                        job_role: str) -> str: ...

def get_fallback_ack(next_stage: StageConfig,
                      candidate_name: str) -> str: ...

def build_role_context(job_role: str, experience_level: str) -> str: ...
def build_personality_note(candidate_name, job_role, experience_level, role_context) -> str: ...
def build_post_interview_feedback_prompt() -> str: ...
def build_agent_note_instruction(stage_type: StageType) -> str: ...   # NEW
```

---

#### `app.py` — Flask server
**Responsibility:** Same routes as MockFlow except:
- **Removed:** `/api/coding/submit` (no coding track).
- **Modified:** `/api/token` — `track` enum now accepts `intro|behavioral|technical_swe|ds_ml|analytics|product` (six values). Maps to `TrackType.<X>` and passes `track` attribute on the LiveKit participant.
- **Modified:** `form.html` GET route serves the new six-track form.
- **Modified:** `/api/skip-stage` — validates `target_stage` against `stage_registry.StageRegistry.get_stage_ids(track)`.
- **New:** `GET /api/eval-report/<interview_id>` — returns JSON `SessionEvalReport` for authenticated owner. 404 if not found.
- **New:** `GET /eval-report/<interview_id>` — renders `templates/eval_report.html`.

All existing endpoints (auth, BYOK keys, /api/upload-resume, /api/extract-topics, /api/conversation/cache, /api/interview/<id>, /api/feedback/*, /past-calls, /interview, /dashboard, /api-keys) carry over identically.

`feedback_cache` and `_feedback_cache` (in-memory dicts) stay.

---

#### `supabase_client.py` — Persistence
**Carry over verbatim:** `_encrypt`, `_decrypt`, `get_user`, `get_user_by_email`, `create_user`, `save_api_keys`, `get_api_keys`, `get_user_interviews`, `get_interview_by_id`, `get_interview_by_room_name`, `save_feedback`, `get_feedback`.

**Modify:** `save_interview()` — accepts new `stage_notes` field and writes to new `stage_notes jsonb` column. Drop `coding_submissions` writes. Drop `track_config.submissions` / `preferred_language` fields.

**Add (Phase 3):**
```python
def save_eval_report(self, user_id: str, interview_id: str,
                     session_report: SessionEvalReport) -> str | None: ...
def save_stage_eval_reports(self, eval_report_id: str, interview_id: str,
                            stage_reports: list[StageEvalReport]) -> bool: ...
def get_eval_report(self, user_id: str, interview_id: str) -> dict | None: ...
def get_stage_eval_reports(self, eval_report_id: str) -> list[dict]: ...
def get_agent_performance_trend(self, user_id: str, n_sessions: int) -> dict: ...
def compare_sessions(self, session_ids: list[str]) -> dict: ...
```

**Phase 1 stubs:** all eval methods exist but `raise NotImplementedError` or `return None` with a log line, so the contract is in place from Phase 1.

---

### 3.2 Carry over verbatim (do not modify in Phase 1)

| File | Notes |
|---|---|
| `worker_manager.py` | Subprocess spawn, env injection, 8s readiness wait, atexit cleanup. Copy line-for-line. |
| `document_processor.py` | PDF/DOCX/MD/TXT extraction, MD5 cache. Pure module. |
| `audio_cache.py` | `WELCOME_AUDIO_FILES` map and `WELCOME_SCRIPTS` map extended to six tracks (see §4.6). Function bodies unchanged. |
| `speech_analytics.py` | Filler/WPM analysis. Pure. |
| `conversation_cache.py` | In-memory conversation store. |
| `auth_helpers.py` | `require_auth`, `get_current_user`, `get_user_id`. |
| `postprocess.py` | `merge_by_agent_turns`, etc. Used by app.py for transcript rendering. |
| All templates except `form.html` (Phase 2) and `eval_report.html` (Phase 3 new). |
| All static CSS/JS assets. |

### 3.3 New (built from scratch)

| File | Phase |
|---|---|
| `stage_registry.py` | Phase 1 (Intro track only) → Phase 2 (all six) |
| `evaluator.py` | Phase 3 |
| `eval_rubrics.py` | Phase 3 |
| `aggregator.py` | Phase 4 |
| `prompt_store.py` | Phase 4 |
| `eval_mcp_server.py` | Phase 4 |
| `templates/eval_report.html` | Phase 3 |
| `supabase_schema.sql` | Phase 1 (with eval tables included from the start; safer to apply once) |
| `generate_welcome_audio.py` | Phase 1 (Intro audio) → Phase 2 (all six tracks) |
| `verify_setup.py` | Phase 1 |

---

## 4. Data Structures

### 4.1 `stage_registry.py`

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Any


class StageType(Enum):
    """The 12 structural archetypes. Every stage in every track is one of these."""
    WELCOME             = "welcome"
    SELF_INTRO          = "self_intro"
    DEPTH_STAGE         = "depth_stage"
    COMPANY_FIT         = "company_fit"
    BEHAVIORAL_Q        = "behavioral_q"
    TECHNICAL_CONCEPTS  = "technical_concepts"
    SYSTEM_DESIGN       = "system_design"
    SQL_PROBLEM         = "sql_problem"
    BUSINESS_CASE       = "business_case"
    PRODUCT_SENSE       = "product_sense"
    ANALYTICAL_METRICS  = "analytical_metrics"
    CLOSING             = "closing"


class TrackType(Enum):
    INTRO          = "intro"
    BEHAVIORAL     = "behavioral"
    TECHNICAL_SWE  = "technical_swe"
    DS_ML          = "ds_ml"
    ANALYTICS      = "analytics"
    PRODUCT        = "product"


@dataclass(frozen=True)
class StageConfig:
    """One instance per stage in a track sequence. Frozen — never mutated at runtime."""
    stage_id: str                       # unique within a track. e.g. "welcome", "behavioral_q_leadership"
    stage_type: StageType
    display_name: str                   # UI label
    time_limit: int                     # seconds
    min_questions: int
    params: dict                        # type-specific. See §4.2 below.
    prompt_template_key: str            # name of template class in prompts.py
    eval_rubric_key: str                # name of rubric class in eval_rubrics.py
    agent_note_schema: dict             # required note field schema. See §4.3.
    is_shared: bool = False             # True for WELCOME, SELF_INTRO, CLOSING


class StageRegistry:
    """Static registry of all track sequences. No instances; class methods only."""
    _SEQUENCES: dict[TrackType, list[StageConfig]] = { ... }  # built in module init

    @classmethod
    def get_stages(cls, track: TrackType) -> list[StageConfig]: ...

    @classmethod
    def get_stage_ids(cls, track: TrackType) -> list[str]: ...

    @classmethod
    def get_stage_by_id(cls, track: TrackType, stage_id: str) -> StageConfig | None: ...

    @classmethod
    def get_display_name(cls, track: TrackType) -> str: ...
```

### 4.2 `StageConfig.params` schemas per StageType

| StageType | params keys | Example values |
|---|---|---|
| WELCOME | `track_name`, `tone`, `framework_hint`, `document_injection` | `"Intro Call"`, `"warm professional"`, `None`, `[]` |
| SELF_INTRO | `depth_expectation`, `focus_hint`, `document_injection` | `"brief"`, `"motivation + background"`, `[]` |
| DEPTH_STAGE | `focus_area`, `document_injection` | `"general"\|"technical"\|"analytical"`, `["resume"]` |
| COMPANY_FIT | `document_injection` | `["resume","jd"]` |
| BEHAVIORAL_Q | `competency`, `framework`, `depth_setting`, `document_injection` | `"leadership"\|"conflict"\|"failure"\|"ambiguity"`, `"amazon"\|"google"\|"meta"\|"generic"`, `"light"\|"medium"\|"deep"`, `["resume","jd"]` |
| TECHNICAL_CONCEPTS | `domain`, `topic_count`, `document_injection` | `"swe"\|"ds_ml"`, `3`, `["resume"]` |
| SYSTEM_DESIGN | `variant`, `document_injection` | `"software"\|"ml_pipeline"`, `["resume","jd"]` |
| SQL_PROBLEM | `difficulty`, `document_injection` | `"easy"\|"medium"\|"hard"`, `[]` |
| BUSINESS_CASE | `case_type`, `document_injection` | `"analytics"\|"strategy"`, `["jd"]` |
| PRODUCT_SENSE | `document_injection` | `["jd"]` |
| ANALYTICAL_METRICS | `document_injection` | `[]` |
| CLOSING | `track_name`, `follow_up_allowed` | `"Intro Call"`, `True` |

`document_injection: list[str]` is a list of injection keys: `"resume"`, `"jd"`, or empty. `InterviewState.get_document_context()` reads this from the current stage and renders the appropriate context block.

### 4.3 Agent Note Schemas (per StageType)

Each `StageConfig.agent_note_schema` is a `dict[field_name → type_hint]`. The agent must populate every field at `transition_stage()` call time. Schema validation runs; missing fields are flagged in the eval report but do not block the transition.

```python
AGENT_NOTE_SCHEMAS: dict[StageType, dict] = {
    StageType.WELCOME: {
        "candidate_state_observed": str,        # "calm" | "nervous" | "uncertain"
        "tone_match_confirmed": bool,
    },
    StageType.SELF_INTRO: {
        "narrative_summary": str,
        "named_companies_or_projects": list[str],
        "contradictions_with_resume": list[str],
        "threads_opened_not_probed": list[str],
        "transition_reason": str,               # "time_pressure" | "depth_achieved" | "min_met"
    },
    StageType.DEPTH_STAGE: {
        "project_or_role_discussed": str,
        "impact_claims_made": list[str],
        "threads_opened_not_probed": list[str],
        "depth_assessment": str,                # "surface" | "moderate" | "deep"
        "transition_reason": str,
        "flags_for_later_stages": list[str],
    },
    StageType.COMPANY_FIT: {
        "motivation_themes": list[str],
        "company_facts_cited": list[str],
        "candidate_questions_asked": list[str],
        "alignment_assessment": str,            # "weak"|"moderate"|"strong"
        "transition_reason": str,
    },
    StageType.BEHAVIORAL_Q: {
        "competency_targeted": str,
        "question_asked": str,
        "star_components_covered": dict,        # {"S":bool,"T":bool,"A":bool,"R":bool}
        "agency_signal": str,                   # "I"|"we"|"unclear"
        "impact_quantified": bool,
        "second_example_asked": bool,
        "transition_reason": str,
    },
    StageType.TECHNICAL_CONCEPTS: {
        "topics_covered": list[str],
        "depth_assessment_per_topic": dict,     # {topic: "surface"|"moderate"|"deep"}
        "candidate_uncertainty_moments": list[str],
        "transition_reason": str,
    },
    StageType.SYSTEM_DESIGN: {
        "problem_framed": str,
        "components_discussed": list[str],
        "tradeoffs_named_by_candidate": list[str],
        "bottlenecks_identified_by_candidate": list[str],
        "scale_pushed_to": str,
        "transition_reason": str,
    },
    StageType.SQL_PROBLEM: {
        "problem_summary": str,
        "approach_clarity_before_writing": bool,
        "edge_cases_candidate_named": list[str],
        "optimization_discussed": bool,
        "transition_reason": str,
    },
    StageType.BUSINESS_CASE: {
        "case_summary": str,
        "frameworks_used_by_candidate": list[str],
        "metrics_proposed_by_candidate": list[str],
        "assumptions_stated": list[str],
        "recommendation_clarity": str,          # "clear"|"hedged"|"absent"
        "transition_reason": str,
    },
    StageType.PRODUCT_SENSE: {
        "problem_brief": str,
        "user_segments_identified": list[str],
        "features_proposed": list[str],
        "prioritization_framework_used": str,
        "transition_reason": str,
    },
    StageType.ANALYTICAL_METRICS: {
        "primary_metrics_proposed": list[str],
        "counter_metrics_proposed": list[str],
        "leading_vs_lagging_distinguished": bool,
        "transition_reason": str,
    },
    StageType.CLOSING: {
        "candidate_questions_asked": list[str],
        "wrap_completed_naturally": bool,
    },
}
```

`AgentNote` dataclass:
```python
@dataclass
class AgentNote:
    stage_id: str
    stage_type: StageType
    fields: dict                           # validated against AGENT_NOTE_SCHEMAS[stage_type]
    schema_valid: bool                     # False if any required field missing/wrong type
    schema_errors: list[str] = field(default_factory=list)
    written_at: str = ""                   # ISO timestamp
```

### 4.4 The Six Track Sequences

Defined in `stage_registry.py`. WELCOME, SELF_INTRO, CLOSING are shared across tracks but the **`StageConfig` instances are distinct per track** (different `params.track_name`, different `time_limit` if needed).

```python
# === Track 1: Intro Call ===
INTRO_SEQUENCE = [
    StageConfig("welcome",      StageType.WELCOME,      "Welcome",      time_limit=60,  min_questions=1,
                params={"track_name":"Intro Call","tone":"warm professional","framework_hint":None,"document_injection":[]},
                prompt_template_key="WELCOME_TEMPLATE", eval_rubric_key="WELCOME_RUBRIC",
                agent_note_schema=AGENT_NOTE_SCHEMAS[StageType.WELCOME], is_shared=True),

    StageConfig("self_intro",   StageType.SELF_INTRO,   "Introduction", time_limit=120, min_questions=2,
                params={"depth_expectation":"brief","focus_hint":"motivation + background","document_injection":[]},
                prompt_template_key="SELF_INTRO_TEMPLATE", eval_rubric_key="SELF_INTRO_RUBRIC",
                agent_note_schema=AGENT_NOTE_SCHEMAS[StageType.SELF_INTRO], is_shared=True),

    StageConfig("depth_general",StageType.DEPTH_STAGE,  "Experience",   time_limit=240, min_questions=5,
                params={"focus_area":"general","document_injection":["resume"]},
                prompt_template_key="DEPTH_STAGE_TEMPLATE", eval_rubric_key="DEPTH_STAGE_RUBRIC",
                agent_note_schema=AGENT_NOTE_SCHEMAS[StageType.DEPTH_STAGE]),

    StageConfig("company_fit",  StageType.COMPANY_FIT,  "Company Fit",  time_limit=240, min_questions=3,
                params={"document_injection":["resume","jd"]},
                prompt_template_key="COMPANY_FIT_TEMPLATE", eval_rubric_key="COMPANY_FIT_RUBRIC",
                agent_note_schema=AGENT_NOTE_SCHEMAS[StageType.COMPANY_FIT]),

    StageConfig("closing",      StageType.CLOSING,      "Closing",      time_limit=45,  min_questions=0,
                params={"track_name":"Intro Call","follow_up_allowed":True},
                prompt_template_key="CLOSING_TEMPLATE", eval_rubric_key="CLOSING_RUBRIC",
                agent_note_schema=AGENT_NOTE_SCHEMAS[StageType.CLOSING], is_shared=True),
]

# === Track 2: Behavioral ===
# WELCOME → SELF_INTRO → BEHAVIORAL_Q(leadership) → BEHAVIORAL_Q(conflict) → BEHAVIORAL_Q(failure)
#       → [BEHAVIORAL_Q(ambiguity) if depth=='deep'] → CLOSING
BEHAVIORAL_SEQUENCE = [
    StageConfig("welcome", ...),         # is_shared=True, track_name="Behavioral"
    StageConfig("self_intro", ...),      # is_shared=True
    StageConfig("behavioral_q_leadership", StageType.BEHAVIORAL_Q, "Leadership", 300, 2,
                params={"competency":"leadership","framework":"amazon","depth_setting":"medium","document_injection":["resume","jd"]},
                prompt_template_key="BEHAVIORAL_Q_TEMPLATE", eval_rubric_key="BEHAVIORAL_Q_RUBRIC",
                agent_note_schema=AGENT_NOTE_SCHEMAS[StageType.BEHAVIORAL_Q]),
    StageConfig("behavioral_q_conflict",   StageType.BEHAVIORAL_Q, "Conflict",   300, 2, params={"competency":"conflict",...}, ...),
    StageConfig("behavioral_q_failure",    StageType.BEHAVIORAL_Q, "Failure",    300, 2, params={"competency":"failure",...}, ...),
    StageConfig("behavioral_q_ambiguity",  StageType.BEHAVIORAL_Q, "Ambiguity",  300, 2, params={"competency":"ambiguity",...}, ...),  # optional
    StageConfig("closing", ...),
]

# === Track 3: Technical SWE ===
# WELCOME → SELF_INTRO → DEPTH_STAGE(technical) → TECHNICAL_CONCEPTS(swe) → SYSTEM_DESIGN(software) → CLOSING
TECHNICAL_SWE_SEQUENCE = [...]

# === Track 4: Data Science / ML ===
# WELCOME → SELF_INTRO → DEPTH_STAGE(technical) → TECHNICAL_CONCEPTS(ds_ml) → SYSTEM_DESIGN(ml_pipeline) → CLOSING
DS_ML_SEQUENCE = [...]

# === Track 5: Analytics / BI ===
# WELCOME → SELF_INTRO → DEPTH_STAGE(analytical) → SQL_PROBLEM → BUSINESS_CASE(analytics) → CLOSING
ANALYTICS_SEQUENCE = [...]

# === Track 6: Product / Strategy ===
# WELCOME → SELF_INTRO → DEPTH_STAGE(analytical) → PRODUCT_SENSE → ANALYTICAL_METRICS → BUSINESS_CASE(strategy) → CLOSING
PRODUCT_SEQUENCE = [...]
```

The Behavioral track's optional `behavioral_q_ambiguity` stage is handled by either:
- (Option A, chosen) Building the sequence with all four behavioral stages, then trimming to three at session start based on the `depth` attribute from the form. The trim happens in `agent_worker.run_interview()` before the sequence is handed to `InterviewState`.
- (Option B, rejected) Two separate registry entries for "behavioral_3q" and "behavioral_4q". This duplicates definitions.

### 4.5 Eval Layer Dataclasses (Phase 3)

```python
# evaluator.py

@dataclass
class EvalEvidence:
    quote_from_transcript: str
    critique: str
    turn_index: int | None = None      # index into transcript_slice

@dataclass
class AgentEvalFace:
    scores: dict[str, int]              # dimension → 1..5
    failures: list[EvalEvidence]
    highlights: list[EvalEvidence]
    note_quality_score: int             # 1..5
    overall_pass: bool                  # False if any score < 3

@dataclass
class CandidateFace:
    scores: dict[str, int]              # dimension → 1..5
    strengths: list[str]
    gaps: list[str]
    open_threads: list[str]
    performance_signal: str             # "weak"|"moderate"|"strong"

@dataclass
class StageEvalReport:
    stage_id: str
    stage_type: StageType
    agent_note: AgentNote | None
    programmatic_signals: dict
    agent_face: AgentEvalFace
    candidate_face: CandidateFace

@dataclass
class EvalContextBundle:
    candidate_name: str
    role: str
    experience_level: str
    resume_text: str | None
    job_description: str | None
    company_name: str | None
    company_context: str | None
    track: TrackType
    stage_sequence: list[StageConfig]
    programmatic_session_signals: dict   # WPM avg, filler rate avg, total duration

@dataclass
class SessionEvalReport:
    session_id: str                     # = interview_id
    user_id: str
    track: TrackType
    timestamp: str                       # ISO
    stage_reports: list[StageEvalReport]
    overall_agent_pass: bool             # True iff all stages.agent_face.overall_pass
    overall_candidate_signal: str        # aggregate of stage_reports performance_signal
    rubric_version: int = 1              # bumped if rubrics change
```

`PromptPatch` (Phase 4) lives in `aggregator.py`:
```python
@dataclass
class PromptPatch:
    stage_type: StageType
    stage_id: str | None                # None if patch applies to all stages of this type
    template_class: str                 # e.g. "BEHAVIORAL_Q_TEMPLATE"
    attribute: str                      # "conversation"|"rules"|"style"|"focus_areas"|"transition"
    current_text: str
    proposed_text: str
    reason: str                         # why aggregator proposed this
    session_ids: list[str]              # sessions that triggered it
    severity: str                       # "low"|"medium"|"high"

@dataclass
class AggregationReport:
    generated_at: str
    sessions_analyzed: list[str]
    failure_counts_by_stage_dimension: dict   # {stage_type: {dimension: count}}
    proposed_patches: list[PromptPatch]
```

### 4.6 Welcome audio extension

`audio_cache.py` updated:
```python
WELCOME_AUDIO_FILES = {
    "intro":          "static/audio/welcome_intro.mp3",
    "behavioral":     "static/audio/welcome_behavioral.mp3",
    "technical_swe":  "static/audio/welcome_technical_swe.mp3",
    "ds_ml":          "static/audio/welcome_ds_ml.mp3",
    "analytics":      "static/audio/welcome_analytics.mp3",
    "product":        "static/audio/welcome_product.mp3",
}

WELCOME_SCRIPTS = {
    "intro":          "Welcome to your mock interview. ...",
    "behavioral":     "Welcome to your behavioral mock interview. ...",
    "technical_swe":  "Welcome to your technical software engineering interview. ...",
    "ds_ml":          "Welcome to your data science and ML interview. ...",
    "analytics":      "Welcome to your analytics interview. ...",
    "product":        "Welcome to your product strategy interview. ...",
}
```

`generate_welcome_audio.py` is a one-shot CLI: `python generate_welcome_audio.py --track intro|behavioral|...|all`. Reads OPENAI_API_KEY from env (NOT a user's BYOK key — this is a server-side build-time asset).

---

## 5. Supabase Schema (full SQL — `supabase_schema.sql`)

```sql
-- =============================================================================
-- VoiceLoop Supabase schema
-- Run once on a fresh project. Idempotent IF NOT EXISTS guards included.
-- =============================================================================

-- --- USERS ------------------------------------------------------------------
-- Mirrors Supabase auth.users. Maintained either via trigger or app-side upsert.
CREATE TABLE IF NOT EXISTS users (
    id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       text UNIQUE NOT NULL,
    name        text,
    google_id   text,
    picture_url text,
    created_at  timestamptz DEFAULT now()
);

-- --- BYOK ENCRYPTED API KEYS ------------------------------------------------
CREATE TABLE IF NOT EXISTS user_api_keys (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     uuid UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    livekit_url_encrypted       text NOT NULL,
    livekit_key_encrypted       text NOT NULL,
    livekit_secret_encrypted    text NOT NULL,
    openai_key_encrypted        text NOT NULL,
    deepgram_key_encrypted      text NOT NULL,
    encryption_salt             text DEFAULT 'salt_v1',
    updated_at                  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_api_keys_user ON user_api_keys(user_id);

-- --- INTERVIEWS -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS interviews (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    candidate_name     text,
    candidate_email    text,
    room_name          text,
    job_role           text,
    experience_level   text,
    company_name       text,
    track              text NOT NULL,            -- "intro"|"behavioral"|"technical_swe"|"ds_ml"|"analytics"|"product"
    track_config       jsonb DEFAULT '{}'::jsonb,
    interview_date     timestamptz DEFAULT now(),
    final_stage        text,
    ended_by           text,                     -- "natural_completion"|"user_disconnect"|"timeout"
    skipped_stages     jsonb DEFAULT '[]'::jsonb,
    has_resume         bool DEFAULT false,
    has_jd             bool DEFAULT false,
    conversation       jsonb DEFAULT '{}'::jsonb,
    total_messages     jsonb DEFAULT '{}'::jsonb,
    stage_notes        jsonb DEFAULT '{}'::jsonb,   -- NEW: dict[stage_id → AgentNote]
    metadata           jsonb DEFAULT '{}'::jsonb,
    created_at         timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_interviews_user        ON interviews(user_id);
CREATE INDEX IF NOT EXISTS idx_interviews_room_name   ON interviews(room_name);
CREATE INDEX IF NOT EXISTS idx_interviews_date        ON interviews(interview_date DESC);
CREATE INDEX IF NOT EXISTS idx_interviews_track       ON interviews(track);

-- --- CANDIDATE FEEDBACK (existing, unchanged) -------------------------------
CREATE TABLE IF NOT EXISTS feedback (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    interview_id    uuid NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    feedback_data   jsonb NOT NULL,
    created_at      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_interview ON feedback(interview_id);

-- --- EVAL REPORTS (NEW) -----------------------------------------------------
CREATE TABLE IF NOT EXISTS eval_reports (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interview_id                uuid UNIQUE NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    user_id                     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_eval                jsonb NOT NULL,       -- full SessionEvalReport serialized
    overall_agent_pass          bool,
    overall_candidate_signal    text,                 -- "weak"|"moderate"|"strong"
    rubric_version              int DEFAULT 1,
    created_at                  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_reports_interview ON eval_reports(interview_id);
CREATE INDEX IF NOT EXISTS idx_eval_reports_user      ON eval_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_eval_reports_signal    ON eval_reports(overall_candidate_signal);

-- --- STAGE EVAL REPORTS (NEW, one row per stage per session) ---------------
CREATE TABLE IF NOT EXISTS stage_eval_reports (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_report_id          uuid NOT NULL REFERENCES eval_reports(id) ON DELETE CASCADE,
    interview_id            uuid NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    stage_id                text NOT NULL,
    stage_type              text NOT NULL,
    stage_index             int NOT NULL,                -- position in sequence
    agent_face              jsonb NOT NULL,
    candidate_face          jsonb NOT NULL,
    programmatic_signals    jsonb NOT NULL,
    agent_note              jsonb,                       -- nullable (agent may have failed to write one)
    note_schema_valid       bool DEFAULT true,
    created_at              timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stage_eval_eval_report ON stage_eval_reports(eval_report_id);
CREATE INDEX IF NOT EXISTS idx_stage_eval_interview   ON stage_eval_reports(interview_id);
CREATE INDEX IF NOT EXISTS idx_stage_eval_type        ON stage_eval_reports(stage_type);

-- =============================================================================
-- RLS POLICIES
-- All tables enable RLS. Users can only see their own rows.
-- The service role (used by Flask backend with SUPABASE_SERVICE_KEY) bypasses RLS.
-- =============================================================================

ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_api_keys       ENABLE ROW LEVEL SECURITY;
ALTER TABLE interviews          ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback            ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_reports        ENABLE ROW LEVEL SECURITY;
ALTER TABLE stage_eval_reports  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users self select" ON users
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "users self insert" ON users
    FOR INSERT WITH CHECK (auth.uid() = id);

CREATE POLICY "api_keys self all" ON user_api_keys
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "interviews self all" ON interviews
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "feedback self all" ON feedback
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "eval_reports self all" ON eval_reports
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "stage_eval_reports via eval_report" ON stage_eval_reports
    FOR ALL USING (
        eval_report_id IN (SELECT id FROM eval_reports WHERE user_id = auth.uid())
    );
```

> **Important note on existing MockFlow schemas:** the reference uses a `users` table that is **separate** from `auth.users` (not a mirror). VoiceLoop standardizes on mirroring `auth.users.id` as `users.id` (a more idiomatic Supabase pattern). If the existing Supabase project already has the older schema, run a migration first or use a fresh project. This INIT.md assumes a fresh project.

---

## 6. Environment Variables (`env.template`)

```bash
# ── LiveKit (server-side defaults for testing; production uses BYOK) ─────────
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# ── OpenAI / Deepgram (server-side defaults for build scripts; production uses BYOK) ─
OPENAI_API_KEY=
DEEPGRAM_API_KEY=

# ── Supabase ────────────────────────────────────────────────────────────────
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_KEY=                  # service_role JWT (bypasses RLS — server only)
SUPABASE_ANON_KEY=                     # used by auth_helpers for token validation
SUPABASE_JWT_SECRET=                   # for verifying inbound JWTs (matches Supabase project setting)

# ── Crypto ──────────────────────────────────────────────────────────────────
ENCRYPTION_KEY=                        # 32-byte url-safe base64 Fernet key
FLASK_SECRET_KEY=                      # any 32+ char secret
FERNET_KEY=                            # alias for ENCRYPTION_KEY; keep both in code for clarity

# ── Google OAuth (via Supabase) ─────────────────────────────────────────────
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# ── Eval layer ──────────────────────────────────────────────────────────────
EVAL_PATCHES_DIR=eval_patches/
PROMPT_VERSIONS_DIR=prompt_versions/
EVAL_RUBRIC_VERSION=1

# ── Runtime ─────────────────────────────────────────────────────────────────
FLASK_ENV=development
FLASK_PORT=5000
MAX_CONCURRENT_WORKERS=10
LOG_LEVEL=INFO
```

> **`FERNET_KEY` vs. `ENCRYPTION_KEY`:** the user-supplied spec calls it `FERNET_KEY`; MockFlow's `supabase_client.py` reads `ENCRYPTION_KEY`. We standardize on reading **`FERNET_KEY`** in VoiceLoop's `supabase_client.py` and update `env.template` accordingly. `ENCRYPTION_KEY` is kept as a recognized alias in case of legacy env files.

---

## 7. Phase Plan

Each phase below has: goal, files touched, concrete deliverables, and a testable outcome the human can verify before approving the next phase.

### Phase 1 — Foundation (Adapted Core, Intro Track only)

**Goal:** One voice interview session on the Intro Call track runs end-to-end. Agent notes are written at each transition. Conversation + stage_notes are saved to Supabase. **No eval pipeline yet** — the trigger is stubbed.

**Files built:**
1. `supabase_schema.sql` — entire schema (including eval tables) applied to a fresh Supabase project.
2. `env.template` — full env spec.
3. `requirements.txt` — pinned deps (use MockFlow's verbatim plus `mcp` for Phase 4 — but install lazily, not at top-level import).
4. `stage_registry.py` — Intro Call track only. `StageType`, `TrackType`, `StageConfig`, `StageRegistry`. `AGENT_NOTE_SCHEMAS` dict complete for at least the 5 Intro stage types.
5. `fsm.py` — generic `InterviewState`. Single class. References `StageConfig`.
6. `prompts.py` — `WELCOME_TEMPLATE`, `SELF_INTRO_TEMPLATE`, `DEPTH_STAGE_TEMPLATE`, `COMPANY_FIT_TEMPLATE`, `CLOSING_TEMPLATE`. `TRANSITION_ACKS`, `FALLBACK_ACKS`, `AGENT_NOTE_PROMPTS`, `ROLE_CONTEXT`, `PERSONALITY`. `build_stage_instructions`, `build_agent_note_instruction`, `get_transition_ack`, `get_fallback_ack`, `build_role_context`, `build_personality_note`, `build_post_interview_feedback_prompt`. `POSTINTERVIEWFEEDBACK`, `FEEDBACKSCORES`, `QUESTION_GENERATION` carried over (will be exercised in Phase 2/3 but harmless if present).
7. `agent_worker.py` — full subprocess:
   - Plugin init with **spec-mandated VAD/endpointing settings (0.5/3.0)**.
   - `InterviewAgent` with all 6 tools. `transition_stage` enforces agent note schema.
   - Welcome audio playback in `on_enter`.
   - Fallback timer.
   - CLOSING content-detection + 5s wait + disconnect.
   - `_run_eval_pipeline(session_id)` **stubbed** — logs `"[EVAL] Would run eval pipeline for session={session_id} — stubbed in Phase 1"` and returns.
8. `app.py` — full Flask server. Track enum currently accepts only `"intro"` (form will be expanded in Phase 2).
9. `worker_manager.py`, `document_processor.py`, `audio_cache.py` (Intro entry only initially), `speech_analytics.py`, `conversation_cache.py`, `auth_helpers.py`, `postprocess.py` — copied verbatim from MockFlow.
10. `supabase_client.py` — adapted with `stage_notes` field in `save_interview()`. Eval methods exist but raise `NotImplementedError`.
11. All templates copied from MockFlow. `form.html` patched to show only the Intro Call track option.
12. `static/audio/welcome_intro.mp3` — generated via `generate_welcome_audio.py --track intro` and committed.
13. `verify_setup.py` — checks all env vars present, can connect to Supabase, can read `supabase_schema.sql` tables.

**Testable outcome:**
- `python verify_setup.py` passes.
- `python app.py` boots without error.
- A logged-in user can fill the form, upload a resume, start an Intro Call interview, complete all 5 stages, and see the conversation saved at `/api/interview/<id>`.
- The `interviews.stage_notes` jsonb column contains 4-5 entries (one per stage transition that occurred). `note_schema_valid` is true for all entries where the LLM populated the schema.
- The agent_worker logs show `"[EVAL] Would run eval pipeline ... — stubbed"` after the session saves.
- Welcome audio plays from the cached MP3 within ~50ms of room connect.
- `min_endpointing_delay=0.5` confirmed in the AgentSession constructor.

---

### Phase 2 — All Six Tracks

**Goal:** All six tracks run end-to-end. Agent notes populated per stage type. Stage parameters injected correctly.

**Files touched:**
1. `stage_registry.py` — add `BEHAVIORAL_SEQUENCE`, `TECHNICAL_SWE_SEQUENCE`, `DS_ML_SEQUENCE`, `ANALYTICS_SEQUENCE`, `PRODUCT_SEQUENCE`. Complete `AGENT_NOTE_SCHEMAS` for all 12 stage types.
2. `prompts.py` — add `BEHAVIORAL_Q_TEMPLATE`, `TECHNICAL_CONCEPTS_TEMPLATE`, `SYSTEM_DESIGN_TEMPLATE`, `SQL_PROBLEM_TEMPLATE`, `BUSINESS_CASE_TEMPLATE`, `PRODUCT_SENSE_TEMPLATE`, `ANALYTICAL_METRICS_TEMPLATE`. Add transition + fallback acks per stage type. Add `AGENT_NOTE_PROMPTS` entries for all stage types. Add `QUESTION_GENERATION.system_design_system`, `.sql_problem_system`, `.business_case_system`, `.product_sense_system`, `.analytical_metrics_system`.
3. `agent_worker.py` — `generate_interview_questions()` becomes a dispatch on `current_stage.stage_type`. Handle Behavioral `depth=='deep'` to keep the optional `behavioral_q_ambiguity` stage; else trim it.
4. `app.py` — track enum accepts all 6. Form posts include track-specific params (framework, depth, topics, custom_topics, sql_difficulty, case_type, system_design_variant).
5. `templates/form.html` — full six-track form with conditional sections.
6. `audio_cache.py` — confirm `WELCOME_AUDIO_FILES` and `WELCOME_SCRIPTS` cover all six tracks.
7. `static/audio/welcome_*.mp3` — generate the five new files via `generate_welcome_audio.py --track all`.

**Testable outcome:**
- A logged-in user can pick any of the six tracks, complete a session, and:
  - The right welcome audio plays on connect.
  - Each stage's `agent_note` in `interviews.stage_notes` validates against `AGENT_NOTE_SCHEMAS[stage_type]`.
  - `final_stage` reflects the last stage of the chosen track.
- The Behavioral track with `depth=light|medium` runs 3 behavioral_q stages; `depth=deep` runs 4.
- Stage instructions for Technical SWE TECHNICAL_CONCEPTS show domain=swe and the candidate's selected/extracted topics; DS/ML shows domain=ds_ml.

---

### Phase 3 — Eval Pipeline

**Goal:** After every session, the eval pipeline runs in the background. One `StageEvalReport` per stage. One `SessionEvalReport` per session. Both faces populated. Persisted to Supabase and to `eval_patches/{interview_id}.json`. Visible at `/eval-report/{interview_id}`.

**Files built:**
1. `eval_rubrics.py` — one rubric class per `StageType` (12 total). Each defines:
   - `agent_dimensions: list[str]` (e.g. `["relevance","probing_depth","impact_extraction","resume_utilization","note_quality"]`)
   - `candidate_dimensions: list[str]`
   - `scoring_guidance: dict[dimension → str]` — what 1/3/5 looks like
   - `calibration_examples: list[dict]` — 2-3 few-shot pairs `(input → expected_score)` per dimension, embedded in the prompt
   - `agent_face_skeptical_criteria: list[str]` — things the evaluator must penalize (generic follow-ups, premature transitions, missed threads, etc.)
   - `candidate_face_skeptical_criteria: list[str]`
   - `get_rubric(stage_type: StageType) -> StageRubric` module-level dispatch.
2. `evaluator.py`:
   - All dataclasses from §4.5.
   - `class EvalAgent` — wraps one LLM call. `def evaluate_stage(self, stage_config, transcript_slice, agent_note, context_bundle, prior_signals, programmatic_signals) -> StageEvalReport`. Uses GPT-4o-mini with `temperature=0.3`, `timeout=10`, structured output (response_format JSON schema). System prompt = skeptical evaluator instructions; user prompt assembles all inputs.
   - `class EvalPipeline`:
     - `async def run(session_id: str) -> SessionEvalReport`
     - `_load_session(session_id)` — fetches interview row, parses conversation, stage_notes, track.
     - `_compute_session_signals(conversation)` — pulls from `speech_analytics.analyze_transcript`.
     - `_slice_transcript_by_stage(conversation, stage_sequence)` — splits agent_msgs by their `stage` field into per-stage slices, pairs with adjacent user turns by timestamp.
     - `_compute_stage_signals(slice_, agent_note)` — question count, follow-up rate, depth score sequence, candidate WPM in stage, filler rate in stage.
     - For each stage: `await EvalAgent.evaluate_stage(...)`; append open_threads + agent_note flags to `prior_signals`.
     - Assemble `SessionEvalReport`. Persist to disk (`eval_patches/{session_id}.json`) and Supabase via `supabase_client.save_eval_report` + `save_stage_eval_reports`.
   - `async def _run_eval_pipeline(session_id: str)` — top-level entrypoint wrapped in try/except. Called from `agent_worker.finalize_and_disconnect`.
3. `supabase_client.py` — implement `save_eval_report`, `save_stage_eval_reports`, `get_eval_report`, `get_stage_eval_reports` (de-stub the Phase 1 placeholders).
4. `agent_worker.py` — activate eval trigger:
   ```python
   try:
       asyncio.create_task(_run_eval_pipeline(interview_id))
   except Exception as e:
       logger.error(f"[EVAL] Failed to start eval pipeline: {e}", exc_info=True)
   ```
   Placed inside `finalize_and_disconnect()` after the Supabase save succeeds, and inside `save_transcript_on_disconnect()` after its save succeeds.
5. `app.py` — add:
   ```python
   @app.route('/api/eval-report/<interview_id>')
   @require_auth
   def get_eval_report_api(interview_id): ...

   @app.route('/eval-report/<interview_id>')
   @require_auth
   def eval_report_page(interview_id): ...
   ```
6. `templates/eval_report.html` — two-face viewer. Top-level overall agent pass + candidate signal. Per-stage accordion: programmatic signals chart, agent face block (scores, failures with quotes, highlights, note quality), candidate face block (scores, strengths, gaps, open threads, performance signal). Vanilla JS + the same CSS palette as `feedback.html`.

**Testable outcome:**
- After any session ends, within 30-60 seconds, `eval_patches/{interview_id}.json` exists.
- `eval_reports` and `stage_eval_reports` rows exist in Supabase for that session.
- `/eval-report/{interview_id}` renders both faces for every stage. Failures cite actual quotes from the transcript. Open threads in stage N appear referenced in stage N+1's agent face critique when the agent didn't follow up.
- The eval pipeline can be run standalone for a saved transcript:
  ```bash
  python -c "import asyncio; from evaluator import _run_eval_pipeline; asyncio.run(_run_eval_pipeline('<interview_id>'))"
  ```
- A failed eval (e.g., OpenAI 500) is logged but does **not** crash the worker or prevent session cleanup.

---

### Phase 4 — MCP + Aggregator + PromptStore

**Goal:** Evaluator callable as an MCP server. Aggregator finds patterns across ≥3 sessions and proposes human-reviewable patches. PromptStore can apply and roll back.

**Files built:**
1. `eval_mcp_server.py` — MCP server (`mcp` Python SDK). Exposes:
   - `evaluate_stage(stage_config_json, transcript_slice, agent_note, context_bundle, prior_signals) → StageEvalReport JSON`
   - `evaluate_session(session_id) → SessionEvalReport JSON`
   - `get_open_threads(session_id) → list[str]`
   - `compare_sessions(session_ids: list[str]) → dict` (cross-session per-dimension averages, regression flags)
   - `get_agent_performance_trend(user_id: str, n_sessions: int) → dict` (rolling per-dimension scores over time)
   - Runs via `stdio` transport for local dev; `sse` is a future option.
2. `aggregator.py`:
   - `aggregate_patches(patch_dir: str = "eval_patches/", min_sessions: int = 3) → AggregationReport`
   - Reads all JSON files in `patch_dir`. Groups failures by `(stage_type, dimension)`. Counts. For dimensions where ≥`min_sessions` failures and ≥50% session rate, builds a `PromptPatch`:
     - `current_text` = the relevant template attribute from `prompt_store.PromptStore.get_current()`
     - `proposed_text` = LLM-generated rewrite that addresses the failure pattern (one focused LLM call per patch using a calibrated "you are an interview prompt editor" prompt)
   - CLI: `python aggregator.py --min-sessions 3 --output aggregation_report.json`
3. `prompt_store.py`:
   - `class PromptStore`:
     - `__init__(self, prompts_module="prompts", versions_dir="prompt_versions/")`
     - `snapshot() → int` — writes current state of all template classes to `prompt_versions/v{N}.json` and returns N.
     - `apply_patch(patch: PromptPatch) → int` — snapshots first, then mutates the in-memory module attribute and rewrites the on-disk `prompts.py` only via a regex on the specific attribute; returns new version number.
     - `rollback(target_version: int) → bool` — restores `prompts.py` text from `prompt_versions/v{target_version}.json`.
     - `get_current_version() → int`
     - `list_versions() → list[dict]`
   - Never auto-applies. Tested only with explicit CLI: `python -c "from prompt_store import PromptStore; ps=PromptStore(); ps.apply_patch(...)"`.

**Testable outcome:**
- `python eval_mcp_server.py` starts an MCP stdio server. An MCP client can list tools and call `evaluate_session(<some_id>)` and receive a structured response.
- After running 3+ Intro Call sessions, `python aggregator.py` produces an `AggregationReport` JSON with at least one `PromptPatch` proposal.
- Applying a patch via `prompt_store.apply_patch()` writes `prompt_versions/v2.json`, mutates `prompts.py`, and the next session uses the patched template.
- `prompt_store.rollback(1)` restores the original.

---

## 8. Key Design Decisions (Choice / Rejected Alternative)

### 8.1 Stage Collapse vs. Per-Track Enums
**Chosen:** 12 `StageType` archetypes + `StageConfig` instances + generic FSM.
**Rejected:** MockFlow's per-track enums (`BehavioralStage`, etc.) and state subclasses (`BehavioralInterviewState`, etc.).
**Why:** Six tracks share WELCOME/SELF_INTRO/CLOSING semantically. Per-track enums duplicate definitions, force `agent_worker.py` to branch on `track_type` in every method, and make adding a new track an O(files-touched) change instead of O(1) registry entry. The collapse cost is one extra layer of indirection (`StageConfig` instead of an enum value) — worth it. The 12-archetype set was chosen by enumerating what's structurally distinct across the six tracks: anything reusable becomes one StageType; anything domain-specific is the StageType's `params`.

### 8.2 Single InterviewState (No Subclasses)
**Chosen:** One `InterviewState` dataclass; track-specific fields (`framework`, `topics`, `selected_topics`, etc.) live in `current_stage.params` or in a generic `state.session_metadata: dict`.
**Rejected:** MockFlow's `BehavioralInterviewState(InterviewState)` etc. that add fields per track.
**Why:** Mirrors the StageType collapse. If any field is truly cross-stage (e.g., framework selected at session start), it goes on `InterviewState` directly with a sensible default. If it's stage-specific (e.g., per-question STAR completeness), it's stored in `stage_notes` after that stage completes.

### 8.3 Agent Note Per Transition (Load-Bearing)
**Chosen:** Agent must populate `agent_note` argument to `transition_stage(reason, agent_note)`. Schema validation runs; failure is flagged but does not block transition.
**Rejected:**
- Option A: Schema failure blocks transition. **Why rejected:** A session-blocking schema error is catastrophic — if the LLM gets one field wrong, the interview hangs. The eval pipeline can still grade the agent for missing fields after the fact.
- Option B: No agent note; eval pipeline infers everything from transcript alone. **Why rejected:** The eval needs the gap between agent intent and execution. Without the note, the evaluator can only judge execution, which weakens the agent face by ~40% of its signal.

### 8.4 Eval as Post-Session Batch (Not Streaming)
**Chosen:** One `asyncio.create_task(_run_eval_pipeline(session_id))` fired after Supabase save. Sequential per-stage internally (progressive disclosure).
**Rejected:**
- Option A: Real-time eval that runs alongside the interview. **Why rejected:** Extra LLM calls during live session inflate latency and risk crashing the voice pipeline. Eval is an offline product; coupling it to live runtime is bad architecture.
- Option B: One single batch LLM call that grades the whole session at once. **Why rejected:** GPT-4o-mini's structured output reliability degrades with input size. A per-stage call keeps each prompt tight (one stage's transcript + that stage's rubric) and lets progressive disclosure carry useful context forward via summarized signals instead of raw text.

### 8.5 Skeptical Evaluator System Prompt (Calibrated to Penalize Leniency)
**Chosen:** Evaluator system prompt explicitly enumerates failure modes to penalize and success modes to reward. Calibration examples in the rubric tie 3/5 vs. 4/5 to concrete behaviors.
**Rejected:** Generic "evaluate quality" prompt. **Why rejected:** LLM-as-judge is notoriously lenient and inconsistent without calibration. The skeptical prompt + few-shot examples are what produce useful, non-trivial critiques.

### 8.6 Two-Face Per Stage (Not Per Session)
**Chosen:** `StageEvalReport.agent_face` and `.candidate_face` per stage. Session-level aggregates derived from them.
**Rejected:** One agent_face and one candidate_face per session. **Why rejected:** Stage-level granularity is needed to (a) catch which stage type the agent fails at (drives the aggregator), (b) progressive disclosure across stages, (c) the report UI showing per-stage breakdowns.

### 8.7 MCP Exposure
**Chosen:** Evaluator exposed via MCP server with 5 tools.
**Rejected:** REST API only. **Why rejected:** REST is fine for the eval report viewer, but MCP makes the evaluator callable from any future tool (dashboards, aggregator, CI hooks, third-party clients) without code changes. REST stays on `app.py` for the user-facing report viewer.

### 8.8 No Auto-Apply of Prompt Patches
**Chosen:** `aggregator.py` proposes patches; `prompt_store.apply_patch()` requires explicit human invocation. Snapshot before every apply; full rollback supported.
**Rejected:** Closed-loop auto-tuning. **Why rejected:** Prompt regressions are silent until users complain. Human review of every patch is the only sane policy for v1. Auto-apply is a future v3 feature behind a feature flag.

### 8.9 No Coding Track (V1 Scope)
**Chosen:** Six tracks; no coding interview track in V1.
**Rejected:** Port MockFlow's coding track. **Why rejected:** Coding adds significant scope (Monaco editor frontend, code evaluator LLM prompt, retry logic, `coding_submissions` table) and doesn't exercise the stage collapse pattern in a new way. The architecture supports adding it later as a 7th track with no FSM changes — just new `StageConfig` entries (`CODING_PROBLEM` archetype) and frontend code-editor templates.

### 8.10 VAD Settings — Spec (0.5/3.0) over MockFlow (0.8/4.0)
**Chosen:** `min_endpointing_delay=0.5`, `max_endpointing_delay=3.0` as specified in the build prompt.
**Rejected:** MockFlow's CPU-tuned 0.8/4.0 values.
**Why:** The user-supplied "voice AI correctness — solved problems" section explicitly states 0.5/3.0 with reasoning. MockFlow inflated these due to Render deployment constraints, not interview-pacing correctness. If VoiceLoop later runs on a constrained CPU and we hear sluggishness, we add a `VAD_PROFILE=cpu_constrained` env var; we don't bake the slow defaults in.

### 8.11 Worker Manager 8-Second Wait Stays
**Chosen:** Carry `worker_manager.py` verbatim from MockFlow including the 8s minimum readiness wait.
**Rejected:** Reduce or remove the 8s wait.
**Why:** Silero VAD model load is empirically 5-8s. Skipping the wait causes the agent to attempt LiveKit connect before VAD is ready, which silently corrupts the first audio frame. Even if the model loads faster on some hardware, the 8s wait is harmless (the user has not yet finished the LiveKit room join handshake).

### 8.12 `track_config` jsonb on `interviews` Table
**Chosen:** A `track_config jsonb` column on `interviews` storing `{"framework":..., "depth":..., "topics":..., "generated_questions":..., ...}`.
**Rejected:** Separate tables for per-track config.
**Why:** Track-specific config is set once per session and never queried structurally. JSONB is the right granularity. Per-track tables would explode the schema for diminishing returns.

### 8.13 `auth.users` Mirror Pattern
**Chosen:** `users.id` PK references `auth.users.id`. RLS on every table keyed to `auth.uid()`.
**Rejected:** Separate `users` table with its own PK and a `google_id` lookup (MockFlow's pattern).
**Why:** Supabase's idiomatic auth integration; cleaner RLS; eliminates a JOIN and a sync risk. Migration from MockFlow's pattern is documented in §5.

### 8.14 Place AGENT_NOTE_SCHEMAS in stage_registry.py, Not in evaluator.py
**Chosen:** `AGENT_NOTE_SCHEMAS` lives in `stage_registry.py` next to `StageConfig`.
**Rejected:** Move it to `evaluator.py`.
**Why:** The schema is referenced by the live agent (Phase 1), not by the evaluator (Phase 3). Putting it in `evaluator.py` would force Phase 1 to import `evaluator.py` for the schema dict alone — a layering violation.

---

## 9. Voice AI Correctness Checklist (Carry-Over from MockFlow — Non-Negotiable)

This list mirrors §2 of the build prompt. Every item is a solved problem in MockFlow-AI. VoiceLoop carries them over without simplification. If voice behaviour breaks during development, the first action is line-by-line comparison against the named reference file.

| # | Concern | VoiceLoop file → MockFlow ref | What to copy exactly |
|---|---|---|---|
| 1 | VAD / turn detection | `agent_worker.py` AgentSession init → MockFlow `agent_worker.py` ~1243 | `MultilingualModel()`, `min_endpointing_delay=0.5`, `max_endpointing_delay=3.0`, `allow_interruptions=True` |
| 2 | Interruption (barge-in) | `agent_worker.py` → MockFlow `agent_worker.py` | `min_interruption_duration=0.5`, `discard_audio_if_uninterruptible=True`; **never** add a custom on_interrupt that modifies history |
| 3 | Worker spawn + 8s readiness | `worker_manager.py` → MockFlow verbatim | `_wait_for_worker_ready` 8s minimum with liveness check, atexit cleanup, env-injected keys |
| 4 | STT→LLM→TTS streaming | `agent_worker.py` plugin init | nova-2 + gpt-4o-mini + tts-1; do NOT add buffering between stages |
| 5 | Stage transition acknowledgement | `fsm.py` (`pending_acknowledgement`, `pending_ack_stage`, `transition_acknowledged`); `agent_worker.py` `transition_stage`, `ask_question` | Three-flag queue; injected at `ask_question` as "STAGE TRANSITION — You MUST first say: ..."; for CLOSING, returned directly to speak immediately |
| 6 | Fallback timer | `agent_worker.py` `stage_fallback_timer` | async task, polls every 5s, 50/75/90/100% milestones, `asyncio.CancelledError` caught, `session.say()` wrapped in try/except |
| 7 | CLOSING safety | `agent_worker.py` `on_conversation_item` + `stage_fallback_timer` | 60s hard timeout; content detection on phrases ["thank you" AND "luck", "good luck", "best of luck"]; 5s `asyncio.sleep` before disconnect |
| 8 | Tool call failure handling | every `@function_tool` in `agent_worker.py` | try/except in every tool; always return a string; OpenAI direct calls use `timeout=10` |
| 9 | Question deduplication | `agent_worker.py` `ask_question` | normalize lower+strip+punctuation; check exact match AND substring containment; return "DUPLICATE: ..." or "SIMILAR: ..." prefixed strings — wording is load-bearing for the system prompt |
| 10 | Pre-generated welcome audio | `audio_cache.py` + `agent_worker.py` `on_enter` | MP3 in `static/audio/welcome_{track}.mp3`; fallback to live TTS if missing; do NOT regenerate at session start |

---

## 10. Conventions

- **Python:** 3.11. Type hints throughout (`list[str]`, `dict[str, int]`, `X | None`). Dataclasses (`frozen=True` where mutation would be a bug, e.g. `StageConfig`).
- **Logging:** Every log line includes `[<MODULE>]` prefix. Per-session lines also include the `session_id` (room name) when available: `logger.info(f"[AGENT] [{session_id}] ...")`. Levels: DEBUG for verbose internal state, INFO for major events, WARNING for recoverable issues, ERROR for failures + `exc_info=True`.
- **Async:** Every `asyncio.create_task` call is wrapped in code that either awaits or catches `asyncio.CancelledError` to allow clean shutdown. The eval pipeline trigger is fire-and-forget but the function itself is fully try/except wrapped.
- **No bare `except:`** — always `except Exception as e:` or specific exception types.
- **Direct OpenAI calls** (i.e., not through the LiveKit pipeline): always pass `timeout=10`. The LiveKit pipeline has its own timeouts.
- **No hardcoded enum string values** in code outside `stage_registry.py`. Always reference `TrackType.INTRO.value`, `StageType.WELCOME.value`. Strings in templates and SQL are fine.
- **No copy-paste across stage types.** Anything that varies by stage type goes through `StageConfig.params` + the template renderer. If you find yourself writing `if stage_type == X:` outside the registry/prompts/rubric files, the architecture is being violated.
- **Eval pipeline must be testable standalone.** Given a saved interview JSON (transcript + stage_notes), the pipeline must run with no LiveKit/Flask running. Achieved by having `EvalPipeline.run(session_id)` only depend on `supabase_client` for read access — and that itself can be mocked with a local JSON loader in tests.
- **No auto-apply of prompt patches.** Aggregator output is human-reviewable JSON only. Apply is always explicit.

---

## 11. Out of Scope for V1

- Coding interview track (frontend Monaco editor, code evaluation pipeline, `coding_submissions` table).
- Real-time eval (only post-session).
- Auto-applied prompt patches.
- Multi-language support beyond English-US (Deepgram nova-2 en-US).
- Mobile-native client (web only).
- Voice cloning / custom TTS voices.
- Self-hosted LiveKit (BYOK assumes LiveKit Cloud).

---

## 12. Open Questions for the Human (To Resolve Before Phase 1)

1. **Supabase project:** Fresh project, or migrate the existing MockFlow project? (Answer affects whether §5 SQL is destructive or needs migration steps.)
2. **Repository structure:** Is the VoiceLoop code rooted at `/Users/krantiy/Documents/VoiceLoop/VoiceLoop/`, or directly at `/Users/krantiy/Documents/VoiceLoop/` (i.e. INIT.md is already at the repo root)? This document assumes the former.
3. **Welcome audio voice:** OpenAI `tts-1 alloy` for all six tracks, or different voices per track for variety? Default: alloy for all (consistent with MockFlow).
4. **Behavioral track "depth" mapping:** `light=2 questions, medium=3 questions, deep=4 questions including ambiguity` — confirm. Default in INIT.md: `light→3, medium→3, deep→4`.
5. **Eval failure pass threshold:** "Any score < 3 fails the stage." Confirm, or use a different rule (e.g., average < 3.5)?

---

*End of INIT.md. Awaiting human approval before starting Phase 1.*

# VoiceLoop — Expanded Feature Map V2

> Based on: `voiceloop_feature_map.md` (MockFlow-AI reference analysis)
> Reference implementation: `external-stuff/MockFlow-AI/`
> Status tags: [UNCHANGED] [MODIFIED] [NEW]

---

## Design Decision: Stage Collapse

MockFlow-AI has separate enums, state classes, and prompt logic for each track's stages.
This produces significant duplication: WELCOME in behavioral, WELCOME in technical, WELCOME in coding
are structurally identical — only content parameters differ. Same for CLOSING and SELF_INTRO.

VoiceLoop collapses this via a `StageConfig` + `StageRegistry` pattern.

### Core Abstraction

```
StageType (enum)         — the 12 structural archetypes
StageConfig (dataclass)  — one instance per stage in a track, parameterized
StageRegistry            — maps TrackType → ordered List[StageConfig]
InterviewFSM             — generic; iterates any StageRegistry sequence
```

### The 12 Stage Types

| StageType | Shared Across Tracks | Parameters That Vary |
|---|---|---|
| WELCOME | All tracks | track_name, tone, framework_hint |
| SELF_INTRO | All tracks | depth_expectation, focus_hint |
| DEPTH_STAGE | All tracks | focus_area (general / technical / analytical) |
| COMPANY_FIT | Intro Call | — |
| BEHAVIORAL_Q | Behavioral | competency (leadership / conflict / failure / ambiguity / collaboration) |
| TECHNICAL_CONCEPTS | SWE, DS/ML | domain, topic_list, difficulty |
| SYSTEM_DESIGN | SWE, DS/ML | variant (software / ml_pipeline) |
| SQL_PROBLEM | Analytics | problem_prompt |
| BUSINESS_CASE | Analytics, Product | case_type |
| PRODUCT_SENSE | Product | — |
| ANALYTICAL_METRICS | Product | — |
| CLOSING | All tracks | track_name, follow_up_allowed |

### StageConfig Dataclass

```python
@dataclass
class StageConfig:
    stage_id: str             # unique key: "welcome", "behavioral_q_leadership"
    stage_type: StageType     # one of the 12 types above
    display_name: str         # UI label
    time_limit: int           # seconds
    min_questions: int
    params: dict              # type-specific parameters (competency, topic, etc.)
    prompt_template_key: str  # points to template in prompts.py
    eval_rubric_key: str      # points to rubric in eval_rubrics.py
    agent_note_schema: dict   # required fields the agent note must populate at transition
    is_shared: bool = False   # True for WELCOME, SELF_INTRO, CLOSING
```

### Result

- `fsm.py` becomes a generic FSM that iterates any `List[StageConfig]`
- `prompts.py` is template-based, keyed by `stage_type`, parameters injected at runtime
- `tracks/` directory becomes `stage_registry.py` — one file, all track definitions
- WELCOME and CLOSING are defined once; reused across all tracks with different params
- BEHAVIORAL_Q is defined once; instantiated N times with different `competency` params

---

## Track Definitions (Collapsed Stage Sequences)

### Track 1 — Intro Call
`WELCOME → SELF_INTRO → DEPTH_STAGE → COMPANY_FIT → CLOSING`

### Track 2 — Behavioral
`WELCOME → SELF_INTRO → BEHAVIORAL_Q(leadership) → BEHAVIORAL_Q(conflict) → BEHAVIORAL_Q(failure) → [BEHAVIORAL_Q(ambiguity)] → CLOSING`
Ambiguity stage optional; controlled by `depth` setting from form.

### Track 3 — Technical Voice (SWE)
`WELCOME → SELF_INTRO → DEPTH_STAGE → TECHNICAL_CONCEPTS(swe, topics[]) → SYSTEM_DESIGN(software) → CLOSING`

### Track 4 — Data Science / ML
`WELCOME → SELF_INTRO → DEPTH_STAGE → TECHNICAL_CONCEPTS(ds_ml, topics[]) → SYSTEM_DESIGN(ml_pipeline) → CLOSING`

### Track 5 — Analytics / BI
`WELCOME → SELF_INTRO → DEPTH_STAGE → SQL_PROBLEM → BUSINESS_CASE(analytics) → CLOSING`

### Track 6 — Product / Strategy
`WELCOME → SELF_INTRO → DEPTH_STAGE → PRODUCT_SENSE → ANALYTICAL_METRICS → BUSINESS_CASE(strategy) → CLOSING`

---

## Tier 1 — Core Architecture

### 1.1 · Finite State Machine [MODIFIED]

**What changes:**
- `InterviewStage` enum replaced by `StageType` enum (12 types instead of N per track)
- `InterviewState` dataclass remains but now references `StageConfig` as current stage instead of an enum value
- `STAGE_TIME_LIMITS` and `STAGE_MIN_QUESTIONS` dicts eliminated — values live in `StageConfig` instances
- `transition_to()` becomes generic: advances `current_index` in the registry sequence
- `get_document_context()` logic moves to `StageConfig.params` (each config declares which documents it needs)
- All track-specific FSM subclasses eliminated (`BehavioralInterviewState`, `TechnicalVoiceInterviewState`, etc.)
- One `InterviewState` class handles all tracks

**What stays identical:**
- Progress summary logic (`get_progress_summary()`)
- Time tracking, question counting, timestamp reset on transition
- `forced` and `skipped` transition flags

**Primary File:** `fsm.py` (heavily modified)
**New File:** `stage_registry.py` (replaces `tracks/` directory)

---

### 1.2 · Interview Agent & Tool Orchestration [MODIFIED]

**What changes:**
- `_get_stage_instructions()` becomes generic: reads from `StageConfig.prompt_template_key` + `params`
- At every `transition_stage()` call, agent must populate an **agent note** before transitioning (new requirement — see Eval Layer 2 section)
- `generate_interview_questions()` parameterized by `StageConfig.params` instead of hardcoded track checks
- Agent note is written to `InterviewState.stage_notes: dict[stage_id → AgentNote]`

**What stays identical:**
- All 4 core tool signatures (`transition_stage`, `ask_question`, `assess_response`, `record_response`)
- `_emit_stage_change()` — WebRTC data channel emission
- `on_enter()` / `on_exit()` lifecycle hooks
- Fallback timer mechanism
- Queued acknowledgement mechanism (`pending_acknowledgement`, `pending_ack_stage`, `transition_acknowledged`)
- Deduplication logic in `ask_question()`

**Primary File:** `agent_worker.py` (moderate modifications)

---

### 1.3 · Per-Session Subprocess Worker [UNCHANGED]

`worker_manager.py` carries over verbatim.
- `spawn_worker()`, `_wait_for_worker_ready()`, `cleanup_all_workers()` — no changes
- BYOK env injection pattern — no changes
- 8s Silero VAD warmup wait — no changes

---

### 1.4 · Prompt System [MODIFIED]

**What changes:**
- Stage prompt classes (`WELCOME`, `SELF_INTRO`, etc.) replaced by template classes keyed on `StageType`
- Each template has `{param}` placeholders filled from `StageConfig.params` at runtime
- `build_stage_instructions()` becomes a generic template renderer, not a multi-track router
- `TRANSITION_ACKS` and `FALLBACK_ACKS` remain but are keyed on `stage_type` not hardcoded stage names
- New section added: `AGENT_NOTE_PROMPTS` — per-stage-type instruction to agent for populating its transition note

**What stays identical:**
- `ROLE_CONTEXT` mapping (role keyword → focus areas)
- `PERSONALITY` (experience level → expectation mapping)
- `POSTINTERVIEWFEEDBACK` prompt
- `FEEDBACKSCORES` prompt
- `CODE_EVALUATOR` prompt
- `QUESTION_GENERATION` prompts

**Primary File:** `prompts.py` (restructured, content largely preserved)

---

### 1.5 · Voice Pipeline [UNCHANGED]

LiveKit Agents SDK integration unchanged.

| Component | Provider | Status |
|---|---|---|
| STT | Deepgram Nova-2 | Unchanged |
| LLM | OpenAI GPT-4o-mini | Unchanged |
| TTS | OpenAI TTS-1 | Unchanged |
| VAD | Silero | Unchanged |

---

## Tier 2 — Functional Features

### 2.1 · Interview Tracks [MODIFIED]

**What changes:**
- `tracks/` directory eliminated
- All track definitions moved to `stage_registry.py` as `List[StageConfig]` per `TrackType`
- `get_track_config()` dispatcher replaced by `StageRegistry.get_stages(track: TrackType)`
- Six tracks defined instead of four (adds Data Science/ML, Analytics/BI, Product/Strategy)
- Track count is now extensible — adding a new track means adding a new `List[StageConfig]` in the registry; no other files change

**What stays identical:**
- Track selection via form (UI unchanged)
- Track passed as participant attribute to agent worker
- BYOK key scoping per track session

**Primary File:** `stage_registry.py` (new, replaces `tracks/`)

---

### 2.2 · Resume / JD Document Upload & RAG Injection [UNCHANGED]

`document_processor.py` carries over verbatim.
- PDF, DOCX, MD, TXT extraction — unchanged
- MD5 cache — unchanged
- `/api/upload-resume` Flask endpoint — unchanged
- Stage-gated injection logic moves from `fsm.py` into `StageConfig.params` (which stages need which docs), but the injection mechanism itself is unchanged
- `/api/extract-topics` endpoint — unchanged

---

### 2.3 · Speech Analytics [UNCHANGED]

`speech_analytics.py` carries over verbatim.
- Filler word detection — unchanged
- WPM calculation — unchanged
- Per-turn pace — unchanged

These signals become inputs to the eval layer (programmatic signals passed to LLM judge). No changes to the module itself.

---

### 2.4 · Post-Interview Feedback System [UNCHANGED]

Candidate-facing feedback pipeline unchanged.
- `FEEDBACKSCORES` prompt — unchanged
- `POSTINTERVIEWFEEDBACK` prompt — unchanged
- `/api/feedback` endpoint — unchanged
- Supabase persistence — unchanged

The eval layer produces a separate report (agent conduct + candidate performance). This is not the same as the feedback report and does not replace it.

---

### 2.5 · Adaptive Question & Problem Generation [MODIFIED — minor]

**What changes:**
- Generation calls now read from `StageConfig.params` for topic, difficulty, competency instead of hardcoded track checks
- `generate_interview_questions()` becomes a generic dispatcher that reads stage type and params

**What stays identical:**
- GPT-4o-mini call structure — unchanged
- JSON output schema for behavioral questions, technical questions, coding problems — unchanged
- Session-start generation timing — unchanged

---

### 2.6 · Live Code Evaluation [UNCHANGED]

Coding track not in scope for V1 of VoiceLoop. Code preserved in codebase but not surfaced. If coding track is added in V2, no changes required to this module.

---

### 2.7 · BYOK Architecture [UNCHANGED]

`supabase_client.py` (key storage portions) — unchanged.
- Fernet encryption — unchanged
- `user_api_keys` table — unchanged
- Ephemeral key injection into subprocess env — unchanged
- `/api/user/keys/validate` — unchanged

---

### 2.8 · Session Persistence & Interview History [MODIFIED — minor]

**What changes:**
- `save_interview()` adds a `stage_notes` field — serialized `dict[stage_id → AgentNote]` persisted alongside conversation
- `interviews` Supabase table gets one new column: `stage_notes jsonb`
- New table added: `eval_reports` (see Eval Layer 2 section)

**What stays identical:**
- `save_interview()` core logic — unchanged
- `get_user_interviews()` — unchanged
- `conversation_cache` — unchanged
- `/past-calls` page — unchanged

---

### 2.9 · Pre-Generated Welcome Audio Cache [MODIFIED — minor]

**What changes:**
- `WELCOME_AUDIO_FILES` extended to include new tracks (DS/ML, Analytics, Product)
- Generation script run once per new track added

**What stays identical:**
- `get_welcome_audio_bytes()` — unchanged
- `generate_and_cache_welcome_audio()` — unchanged
- Fallback to live TTS — unchanged

---

## Tier 3 — UI / Integration Layer

### 3.1 · Flask Web Server & Auth [UNCHANGED]

`app.py` auth portions unchanged.
- Google OAuth via Supabase — unchanged
- `@require_auth` decorator — unchanged
- All auth routes — unchanged

### 3.2 · LiveKit Token Generation & Worker Spawning [UNCHANGED]

`/api/token` endpoint logic unchanged. Track and stage params passed as participant attributes — format unchanged.

### 3.3 · Real-Time UI Events via WebRTC Data Channel [UNCHANGED]

All existing event types unchanged.

| Event | Status |
|---|---|
| `stage_change` | Unchanged |
| `coding_problem` | Unchanged (coding track future) |
| `evaluation_result` | Unchanged (coding track future) |
| `user_caption` | Unchanged |

### 3.4 · HTML Templates [MODIFIED — minor]

- `form.html` — updated to include new track options (DS/ML, Analytics, Product)
- `feedback.html` — unchanged (candidate feedback page)
- `eval_report.html` — NEW: displays two-face eval report per session (agent conduct + candidate performance, per stage)
- All other templates — unchanged

---

## Tier 4 — NEW: Eval Layer 2 (Post-Session Stage-Bifurcated Evaluator)

This is VoiceLoop's core differentiator. Not present in MockFlow-AI in any form.

---

### Architecture Overview

The evaluator runs as a **single batch call at the end of every interview session**, triggered asynchronously after the conversation JSON is saved to Supabase. It does not run during the live session and does not touch the voice pipeline.

Despite being one batch call, it is **bifurcated internally by stage** — it evaluates each stage of the interview in sequence, carrying forward context from prior stages (progressive disclosure). The result is a `SessionEvalReport` composed of one `StageEvalReport` per stage, each with two faces: agent conduct and candidate performance.

```
Session ends
    → conversation JSON saved (existing)
    → stage_notes dict saved (new)
    → asyncio.create_task(_run_eval_pipeline(session_id))
        → EvalPipeline.run(session_id)
            → for each stage in order:
                → EvalAgent.evaluate_stage(stage_config, transcript_slice,
                                           agent_note, context_bundle,
                                           prior_stage_signals)
                → StageEvalReport produced
                → prior_stage_signals updated (progressive disclosure)
            → SessionEvalReport assembled from all StageEvalReports
            → written to eval_patches/{session_id}.json
            → persisted to eval_reports Supabase table
```

---

### The Agent Note (Load-Bearing Artifact)

At every `transition_stage()` call, before the FSM advances, the interview agent **must write a structured note** to `InterviewState.stage_notes[stage_id]`. This is enforced by the tool — if the note is missing or fails schema validation, the transition is still allowed but the missing note is flagged in the eval report.

The `agent_note_schema` in each `StageConfig` defines what fields are required. Example for `DEPTH_STAGE`:

```python
agent_note_schema = {
    "project_or_role_discussed": str,        # what the candidate talked about
    "impact_claims_made": list[str],         # specific claims ("reduced latency by 40%")
    "threads_opened_not_probed": list[str],  # candidate mentioned X, agent didn't follow up
    "depth_assessment": str,                 # "surface" | "moderate" | "deep"
    "transition_reason": str,                # "time_pressure" | "depth_achieved" | "min_met"
    "flags_for_later_stages": list[str]      # things the evaluator should check in future stages
}
```

The evaluator reads both the raw transcript and the agent note. The note reveals agent intent. The transcript reveals agent execution. The evaluator can catch the gap between them.

---

### Two-Face Structure Per Stage

Every `StageEvalReport` contains:

```python
@dataclass
class StageEvalReport:
    stage_id: str
    stage_type: StageType
    agent_note: AgentNote | None          # None if agent failed to write one
    programmatic_signals: dict            # computed before LLM call

    agent_face: AgentEvalFace
    candidate_face: CandidateFace

@dataclass
class AgentEvalFace:
    scores: dict[str, int]               # dimension → 1–5
    failures: list[EvalEvidence]         # {quote_from_transcript, critique}
    highlights: list[EvalEvidence]       # things the agent did well
    note_quality_score: int              # 1–5: was the agent note accurate and complete?
    overall_pass: bool                   # fails if any score < 3

@dataclass
class CandidateFace:
    scores: dict[str, int]               # dimension → 1–5
    strengths: list[str]
    gaps: list[str]
    open_threads: list[str]              # things candidate mentioned, didn't elaborate
    performance_signal: str              # "weak" | "moderate" | "strong"
```

---

### Context Bundle (Progressive Disclosure)

The evaluator receives a `ContextBundle` assembled before the batch run. It contains everything the interview agent had — so the evaluator can judge whether the agent used its context appropriately.

```python
@dataclass
class EvalContextBundle:
    candidate_name: str
    role: str
    experience_level: str
    resume_text: str | None
    job_description: str | None
    company_name: str | None
    company_context: str | None           # scraped or user-provided
    track: TrackType
    stage_sequence: list[StageConfig]
    programmatic_session_signals: dict    # WPM, filler rate, total duration, etc.
```

**Progressive disclosure** means each `StageEvalReport` also receives `prior_stage_signals` — a growing dict that accumulates:
- open threads flagged by prior candidate face evals
- depth signals from prior stages (candidate's WPM in SELF_INTRO vs. DEPTH_STAGE — a drop signals discomfort)
- agent note flags from prior stages (things agent said it would probe in later stages)
- candidate performance signals from prior stages

By COMPANY_FIT or BEHAVIORAL_Q stage 3, the evaluator has a multi-stage picture of the candidate and can cross-reference claimed values against demonstrated behavior.

---

### Per Stage-Type Evaluation Dimensions

#### WELCOME
**Programmatic:** agent first-turn length (word count), session start-to-first-response latency
**Agent face dimensions:** verbosity (1–5), profile extraction accuracy (verifiable against form data), tone calibration
**Candidate face dimensions:** engagement signal (response length vs. expected baseline), early filler rate

#### SELF_INTRO
**Programmatic:** candidate turn duration, WPM vs. session average (faster = rehearsed), filler rate
**Agent face dimensions:** space_given (did it interrupt the narrative?), thread_capture (did it log interesting threads in agent note?), contradiction_detection (did it flag resume contradictions?)
**Candidate face dimensions:** narrative_coherence, role_relevance, specificity (named real things vs. vague), enthusiasm_signal, contradiction_flags

#### DEPTH_STAGE
**Programmatic:** question count, follow-up rate (follow-ups / total questions), candidate turn length average, depth score sequence from assess_response
**Agent face dimensions:** relevance (did it probe the right project given JD?), probing_depth (follow-up rate score), impact_extraction (did it get specific numbers?), resume_utilization (did it probe resume items candidate didn't mention?), note_quality
**Candidate face dimensions:** STAR_completeness, ownership_clarity, impact_specificity, scope_accuracy (vs. experience level), open_threads (for progressive disclosure to later stages)

#### COMPANY_FIT
**Programmatic:** candidate turn length on motivation questions, number of candidate questions asked
**Agent face dimensions:** motivation_probing (did it push past the first rehearsed answer?), company_contextualization, space_for_candidate_questions
**Candidate face dimensions:** research_quality (did they cite specific company knowledge?), motivation_authenticity, value_alignment_evidence, candidate_question_quality (insightful vs. generic)

#### BEHAVIORAL_Q (per competency)
**Programmatic:** question count, STAR probe count (how many times agent asked for missing component), candidate turn length
**Agent face dimensions:** example_extraction (did it get a specific story?), STAR_enforcement (did it probe missing components?), scope_probing (scale, decision authority), second_example_judgment (did it know when to ask for another?)
**Candidate face dimensions:** STAR_completeness (per component: S, T, A, R each scored), competency_authenticity (did the example actually demonstrate the competency?), agency_evidence (I vs. we), impact_scale, principle_alignment (cross-referenced with JD/company values)

Special per-competency calibration:
- **Leadership:** ownership and influence scope
- **Conflict:** honesty (self-implication) and resolution sophistication
- **Failure:** failure authenticity (real consequences) and applied learning evidence
- **Ambiguity:** decision quality under uncertainty and assumption transparency

#### TECHNICAL_CONCEPTS
**Programmatic:** question count, candidate response length per question (proxy for comfort), depth score sequence
**Agent face dimensions:** difficulty_calibration (appropriate to experience level?), edge_probing (did it probe beyond surface answers?), topic_coverage (vs. JD requirements), adaptive_difficulty (did it adjust based on performance?)
**Candidate face dimensions:** conceptual_accuracy (LLM judge with domain calibration), depth_vs_breadth_balance, tradeoff_awareness, uncertainty_handling (honest vs. bluff), communication_clarity (could a non-expert follow?)

#### SYSTEM_DESIGN
**Programmatic:** candidate total speaking time in stage, question count
**Agent face dimensions:** requirements_space (did it let candidate clarify before designing?), bottleneck_probing, scale_pushing, component_probing (did it drill into hand-waved components?)
**Candidate face dimensions:** structured_approach (requirements → high-level → components → deep-dive), requirement_clarification (did they ask questions?), scalability_reasoning, tradeoff_articulation, component_accuracy, back_of_envelope_attempt

#### SQL_PROBLEM
**Programmatic:** candidate response length, time to first approach statement
**Agent face dimensions:** problem_clarity (was the problem unambiguous?), optimization_probing, edge_case_probing (NULLs, duplicates, ties)
**Candidate face dimensions:** query_correctness, approach_clarity (explained before writing?), optimization_awareness, edge_case_handling

#### BUSINESS_CASE / STRATEGY_CASE
**Programmatic:** candidate speaking time, number of distinct frameworks mentioned
**Agent face dimensions:** context_sufficiency (enough info given?), assumption_challenging, quantification_pushing, recommendation_pressing (did it demand a conclusion?)
**Candidate face dimensions:** problem_decomposition, metric_selection_rationale, assumption_transparency, quantification_instinct, recommendation_clarity, business_judgment (does it make sense given company context?)

#### PRODUCT_SENSE
**Programmatic:** candidate speaking time, number of user segments mentioned
**Agent face dimensions:** brief_clarity, user_research_probing, prioritization_probing, feasibility_challenging
**Candidate face dimensions:** user_empathy, problem_definition_quality, prioritization_framework, metric_selection, feasibility_consideration

#### ANALYTICAL_METRICS
**Programmatic:** number of metrics named, number of counter-metrics named
**Agent face dimensions:** success_definition_probing (did it ask what success means before metrics?), metric_challenging, experimentation_probing
**Candidate face dimensions:** counter_metric_awareness, leading_vs_lagging_distinction, measurement_feasibility, business_impact_linkage

#### CLOSING
**Programmatic:** number of candidate questions asked, session total duration
**Agent face dimensions:** closing_naturalness, summary_quality, candidate_question_space
**Candidate face dimensions:** question_quality (insightful vs. generic), graceful_wrap

---

### What Is LLM-as-Judge vs. Programmatic

| Signal | Method |
|---|---|
| Time in stage | Programmatic — FSM timestamps |
| Question count | Programmatic — FSM state |
| Follow-up rate | Programmatic — question log ratio |
| Filler word rate | Programmatic — speech_analytics |
| WPM and pace variance | Programmatic — speech_analytics |
| Turn length distribution | Programmatic — token counts per turn |
| Depth score sequence | Programmatic — assess_response log |
| Agent note completeness | Programmatic — schema validation |
| Conceptual accuracy | LLM judge (domain-calibrated per stage type) |
| STAR completeness | LLM judge |
| Narrative coherence | LLM judge |
| Motivation authenticity | LLM judge |
| Follow-up quality | LLM judge |
| Ownership vs. team credit | LLM judge |
| Open threads | LLM judge → structured list output |
| Trade-off awareness | LLM judge |
| Note quality assessment | LLM judge (meta-evaluation of agent note) |

All programmatic signals are passed to the LLM judge as part of its context. The judge contextualizes rather than recomputes them.

---

### Evaluator Isolation Constraint

The evaluator LLM call sees only:
- Raw dialogue turns (agent text + candidate text) for the specific stage
- Agent note for that stage
- Context bundle (resume, JD, role, experience level, company context)
- Prior stage signals (progressive disclosure)
- Programmatic signals for that stage
- Stage objectives and rubric

It never sees:
- Tool call internals
- FSM state or transition logic
- Other agents' system prompts
- assess_response internal scores (these appear only as programmatic signals, not as reasoning)

Same model as the interview agent (GPT-4o-mini) is fine. Different system prompt is mandatory.

---

### MCP Exposure

The eval pipeline is exposed as an MCP server with the following tools:

| Tool | Inputs | Output |
|---|---|---|
| `evaluate_stage` | stage_config, transcript_slice, agent_note, context_bundle, prior_signals | StageEvalReport |
| `evaluate_session` | session_id | SessionEvalReport (calls evaluate_stage per stage internally) |
| `get_open_threads` | session_id | list of candidate threads opened and not closed |
| `compare_sessions` | session_ids list | cross-session pattern report |
| `get_agent_performance_trend` | user_id, n_sessions | trend data per eval dimension over time |

This makes the evaluator independently callable by: the interview platform, a dashboard tool, the aggregator, and any future tooling without code changes.

---

### New Files for Eval Layer 2

| File | Purpose |
|---|---|
| `evaluator.py` | `EvalPipeline`, `EvalAgent`, `evaluate_stage()`, `evaluate_session()` |
| `eval_rubrics.py` | Per-stage-type rubric definitions, dimension names, scoring guidance, few-shot examples for LLM calibration |
| `aggregator.py` | Reads N `SessionEvalReport` files, finds patterns, proposes `PromptPatch` objects |
| `prompt_store.py` | Versioned wrapper around `prompts.py`, `apply_patch()`, `rollback()`, version log |
| `eval_mcp_server.py` | MCP server exposing eval tools |
| `templates/eval_report.html` | Two-face eval report viewer, per-stage breakdown, programmatic signal charts |

---

### New Supabase Tables

```sql
-- stores one row per session
CREATE TABLE eval_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interview_id uuid REFERENCES interviews(id) ON DELETE CASCADE,
    user_id uuid REFERENCES auth.users(id),
    session_eval jsonb NOT NULL,     -- full SessionEvalReport serialized
    overall_agent_pass bool,
    overall_candidate_signal text,   -- "weak" | "moderate" | "strong"
    created_at timestamptz DEFAULT now()
);

-- stores one row per stage per session (for querying by stage type)
CREATE TABLE stage_eval_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_report_id uuid REFERENCES eval_reports(id) ON DELETE CASCADE,
    interview_id uuid REFERENCES interviews(id),
    stage_id text NOT NULL,
    stage_type text NOT NULL,
    agent_face jsonb NOT NULL,
    candidate_face jsonb NOT NULL,
    programmatic_signals jsonb NOT NULL,
    created_at timestamptz DEFAULT now()
);
```

---

## File Status Summary

| File | Status | Notes |
|---|---|---|
| `fsm.py` | MODIFIED | Generic FSM, references StageConfig not hardcoded enums |
| `agent_worker.py` | MODIFIED | Agent note writing at transition, eval trigger hook, generic instruction builder |
| `prompts.py` | MODIFIED | Template-based by stage_type, AGENT_NOTE_PROMPTS section added |
| `app.py` | MODIFIED | New tracks in form, /api/eval-report endpoint added |
| `supabase_client.py` | MODIFIED | stage_notes field in save_interview, new eval table methods |
| `stage_registry.py` | NEW | Replaces tracks/ directory. All StageConfig definitions per track |
| `evaluator.py` | NEW | EvalPipeline, EvalAgent, two-face evaluation logic |
| `eval_rubrics.py` | NEW | Per-stage-type rubrics, scoring dimensions, LLM calibration examples |
| `aggregator.py` | NEW | Cross-session pattern analysis, PromptPatch proposals |
| `prompt_store.py` | NEW | Versioned prompt management, apply_patch, rollback |
| `eval_mcp_server.py` | NEW | MCP server for eval tool exposure |
| `templates/eval_report.html` | NEW | Two-face eval report viewer |
| `templates/form.html` | MODIFIED | New track options |
| `worker_manager.py` | UNCHANGED | |
| `document_processor.py` | UNCHANGED | |
| `audio_cache.py` | UNCHANGED (minor extension) | New track welcome audio files added |
| `speech_analytics.py` | UNCHANGED | |
| `conversation_cache.py` | UNCHANGED | |
| `auth_helpers.py` | UNCHANGED | |
| `templates/index.html` | UNCHANGED | |
| `templates/interview.html` | UNCHANGED | |
| `templates/dashboard.html` | UNCHANGED | |
| `templates/past_calls.html` | UNCHANGED | |
| `templates/feedback.html` | UNCHANGED | |
| `templates/auth_callback.html` | UNCHANGED | |
| `templates/api_keys.html` | UNCHANGED | |
| `tracks/` (directory) | DELETED | Replaced by stage_registry.py |

---

*VoiceLoop Feature Map V2 — generated 2026-05-10*

# VoiceLoop — Post-Session Stage-Bifurcated Evaluator: Design Spec

## What This Is

A post-session evaluator that runs as a single batch call after every interview ends.
It is internally bifurcated by stage — each stage is evaluated in sequence, with context
carrying forward from prior stages (progressive disclosure). Each stage produces a
two-faced report: one grading the interview agent's conduct, one grading the candidate's
performance.

Nothing in the live voice pipeline changes. The evaluator is fully decoupled.

---

## How It Triggers

After the interview ends and the conversation JSON is saved to Supabase, a single async
background task is fired from `agent_worker.py`:

```python
asyncio.create_task(_run_eval_pipeline(session_id))
```

This runs after the participant has disconnected. Any exception is caught and logged —
it must never affect session cleanup or the existing candidate feedback flow.

---

## The Agent Note (Prerequisite)

At every `transition_stage()` call, before the FSM advances, the interview agent writes
a structured note to `InterviewState.stage_notes[stage_id]`. The required fields are
defined per stage type in `StageConfig.agent_note_schema`.

Example schema for `DEPTH_STAGE`:
```python
{
    "project_or_role_discussed": str,
    "impact_claims_made": list[str],
    "threads_opened_not_probed": list[str],
    "depth_assessment": "surface" | "moderate" | "deep",
    "transition_reason": "time_pressure" | "depth_achieved" | "min_met",
    "flags_for_later_stages": list[str]
}
```

If the agent fails schema validation, the transition still proceeds but the missing note
is flagged in the eval report. The note is persisted alongside the conversation in
Supabase (`stage_notes jsonb` column on `interviews` table).

The note is load-bearing for the evaluator. It reveals agent intent. The transcript
reveals agent execution. The evaluator catches the gap between them.

---

## Eval Pipeline Flow

```
Session ends
  → conversation JSON + stage_notes saved to Supabase
  → asyncio.create_task(_run_eval_pipeline(session_id))
      → EvalPipeline.run(session_id)
          → assemble EvalContextBundle (resume, JD, role, company, experience level)
          → compute programmatic session signals (WPM, filler rate, total duration)
          → prior_stage_signals = {}
          → for each stage in session order:
              → slice transcript to this stage's turns
              → retrieve agent_note for this stage
              → compute programmatic signals for this stage
              → call EvalAgent.evaluate_stage(
                    stage_config, transcript_slice, agent_note,
                    context_bundle, prior_stage_signals
                 )
              → StageEvalReport produced
              → append candidate open_threads to prior_stage_signals
              → append agent note flags to prior_stage_signals
          → SessionEvalReport assembled from all StageEvalReports
          → write to eval_patches/{session_id}.json
          → persist to Supabase eval_reports + stage_eval_reports tables
```

---

## Two-Face Structure Per Stage

Every `StageEvalReport` contains both faces. The evaluator LLM call produces both in
one structured output per stage.

```python
@dataclass
class StageEvalReport:
    stage_id: str
    stage_type: StageType
    agent_note: AgentNote | None        # None if agent failed to write one
    programmatic_signals: dict          # computed before LLM call, passed as context

    agent_face: AgentEvalFace
    candidate_face: CandidateFace

@dataclass
class AgentEvalFace:
    scores: dict[str, int]             # dimension → 1–5
    failures: list[EvalEvidence]       # {quote_from_transcript, critique}
    highlights: list[EvalEvidence]     # things the agent did well
    note_quality_score: int            # 1–5: was the note accurate and complete?
    overall_pass: bool                 # False if any score < 3

@dataclass
class CandidateFace:
    scores: dict[str, int]             # dimension → 1–5
    strengths: list[str]
    gaps: list[str]
    open_threads: list[str]            # threads candidate opened, didn't elaborate
    performance_signal: str            # "weak" | "moderate" | "strong"

@dataclass
class SessionEvalReport:
    session_id: str
    track: TrackType
    timestamp: str
    stage_reports: list[StageEvalReport]
    overall_agent_pass: bool           # True only if all stages pass
    overall_candidate_signal: str      # aggregate of per-stage performance_signal
```

---

## Context Bundle (Progressive Disclosure)

The evaluator gets the same context the interview agent had — so it can judge whether
the agent used that context appropriately, and whether the candidate's answers were
adequate given the role.

```python
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
    programmatic_session_signals: dict   # WPM, filler rate, total duration
```

**Progressive disclosure** means each stage evaluation also receives `prior_stage_signals`
accumulated from all prior stages:
- open threads flagged by earlier candidate face evals
- candidate WPM trend (drop in a later stage signals discomfort with that topic)
- agent note flags from earlier stages (things the agent said it would probe later)
- candidate performance signals from earlier stages

By the third or fourth stage, the evaluator has a cross-stage picture and can
cross-reference claimed values against demonstrated behavior.

---

## Per Stage-Type Evaluation Dimensions

### WELCOME / GREETING
**Programmatic:** agent first-turn word count, session start-to-first-response latency
**Agent face:** verbosity, profile_extraction_accuracy, tone_calibration
**Candidate face:** engagement_signal, early_filler_rate

### SELF_INTRO
**Programmatic:** candidate turn duration, WPM vs session average, filler rate
**Agent face:** space_given, thread_capture (logged in note?), contradiction_detection
**Candidate face:** narrative_coherence, role_relevance, specificity, enthusiasm_signal, contradiction_flags

### DEPTH_STAGE
**Programmatic:** question count, follow_up_rate (follow-ups / total questions), candidate turn length average, depth score sequence
**Agent face:** relevance, probing_depth, impact_extraction, resume_utilization, note_quality
**Candidate face:** STAR_completeness, ownership_clarity, impact_specificity, scope_accuracy, open_threads

### COMPANY_FIT
**Programmatic:** candidate turn length on motivation questions, number of candidate questions asked
**Agent face:** motivation_probing, company_contextualization, space_for_candidate_questions
**Candidate face:** research_quality, motivation_authenticity, value_alignment_evidence, candidate_question_quality

### BEHAVIORAL_Q (per competency — leadership / conflict / failure / ambiguity)
**Programmatic:** question count, STAR probe count, candidate turn length
**Agent face:** example_extraction, STAR_enforcement, scope_probing, second_example_judgment
**Candidate face:** STAR_completeness (S/T/A/R each scored), competency_authenticity, agency_evidence, impact_scale, principle_alignment

Additional calibration per competency:
- leadership → ownership and influence scope
- conflict → honesty (self-implication), resolution sophistication
- failure → failure authenticity (real consequences), applied learning evidence
- ambiguity → decision quality under uncertainty, assumption transparency

### TECHNICAL_CONCEPTS
**Programmatic:** question count, candidate response length per question, depth score sequence
**Agent face:** difficulty_calibration, edge_probing, topic_coverage, adaptive_difficulty
**Candidate face:** conceptual_accuracy, depth_vs_breadth_balance, tradeoff_awareness, uncertainty_handling, communication_clarity

### SYSTEM_DESIGN
**Programmatic:** candidate total speaking time, question count
**Agent face:** requirements_space, bottleneck_probing, scale_pushing, component_probing
**Candidate face:** structured_approach, requirement_clarification, scalability_reasoning, tradeoff_articulation, component_accuracy, back_of_envelope_attempt

### SQL_PROBLEM
**Programmatic:** candidate response length, time to first approach statement
**Agent face:** problem_clarity, optimization_probing, edge_case_probing
**Candidate face:** query_correctness, approach_clarity, optimization_awareness, edge_case_handling

### BUSINESS_CASE / STRATEGY_CASE
**Programmatic:** candidate speaking time, distinct frameworks mentioned
**Agent face:** context_sufficiency, assumption_challenging, quantification_pushing, recommendation_pressing
**Candidate face:** problem_decomposition, metric_selection_rationale, assumption_transparency, quantification_instinct, recommendation_clarity, business_judgment

### PRODUCT_SENSE
**Programmatic:** candidate speaking time, user segments mentioned
**Agent face:** brief_clarity, user_research_probing, prioritization_probing, feasibility_challenging
**Candidate face:** user_empathy, problem_definition_quality, prioritization_framework, metric_selection, feasibility_consideration

### ANALYTICAL_METRICS
**Programmatic:** metrics named, counter-metrics named
**Agent face:** success_definition_probing, metric_challenging, experimentation_probing
**Candidate face:** counter_metric_awareness, leading_vs_lagging_distinction, measurement_feasibility, business_impact_linkage

### CLOSING
**Programmatic:** candidate questions asked, session total duration
**Agent face:** closing_naturalness, summary_quality, candidate_question_space
**Candidate face:** question_quality, graceful_wrap

---

## Programmatic vs LLM-as-Judge

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
| Note quality | LLM judge (meta-evaluation of agent note vs. transcript) |

All programmatic signals are passed to the LLM judge as context. The judge
contextualizes rather than recomputes them.

---

## New Files

### `evaluator.py`
Core eval module. No LiveKit, no WebRTC, no voice.

- `EvalPipeline` — orchestrates the full session eval: assembles context bundle, iterates stages, accumulates prior signals, assembles SessionEvalReport
- `EvalAgent` — wraps the LLM call per stage. Receives transcript slice, agent note, context bundle, prior signals, programmatic signals, and stage rubric. Returns StageEvalReport.
- `_run_eval_pipeline(session_id)` — async entry point called from agent_worker.py. Try/except wrapped. Writes output to disk and Supabase.
- All eval dataclasses: `StageEvalReport`, `AgentEvalFace`, `CandidateFace`, `EvalEvidence`, `SessionEvalReport`, `EvalContextBundle`, `AgentNote`

### `eval_rubrics.py`
Per-stage-type rubric definitions.

- One rubric class per StageType (12 total)
- Each rubric defines: dimension names, scoring guidance (what 1/3/5 looks like), calibration examples (few-shot for LLM), agent face dimensions, candidate face dimensions
- `get_rubric(stage_type: StageType) -> StageRubric`

### `aggregator.py`
Runs manually or on cron. Never called during live sessions.

- `aggregate_patches(patch_dir: str, min_sessions: int = 5) -> AggregationReport`
- Reads all `SessionEvalReport` JSON files
- Groups failures by stage_type and dimension across sessions
- Produces `AggregationReport` with `proposed_patches: list[PromptPatch]` per affected stage
- `PromptPatch` fields: `stage_type`, `stage_id`, `attribute` (conversation/rules/style/transition), `current_text`, `proposed_text`, `reason`, `session_ids` that triggered it
- Does not apply patches. Human-reviewable output only.

### `prompt_store.py`
Versioned wrapper around the prompt system.

- `PromptStore` — loads current stage template attributes at init, tracks version history in `prompt_versions/`
- `apply_patch(patch: PromptPatch) -> bool` — writes proposed rewrite, logs change with timestamp and triggering session IDs
- `rollback(version: int)` — restores to a prior version
- `get_current_version() -> int`
- No auto-apply. All patches applied only after human review and explicit call.

### `eval_mcp_server.py`
Exposes the eval pipeline as an MCP server.

| Tool | Inputs | Output |
|---|---|---|
| `evaluate_stage` | stage_config, transcript_slice, agent_note, context_bundle, prior_signals | StageEvalReport |
| `evaluate_session` | session_id | SessionEvalReport |
| `get_open_threads` | session_id | list[str] |
| `compare_sessions` | session_ids | cross-session pattern report |
| `get_agent_performance_trend` | user_id, n_sessions | trend per eval dimension |

---

## Modified Files

### `agent_worker.py`
Two additions only:

1. **Agent note at every `transition_stage()` call** — before FSM advances, agent populates `InterviewState.stage_notes[stage_id]` against the schema in `StageConfig.agent_note_schema`. Schema validation runs; failure is logged but does not block transition.

2. **Eval trigger hook after session save** — after conversation JSON and stage_notes are saved to Supabase:
```python
asyncio.create_task(_run_eval_pipeline(session_id))
```
Wrapped in try/except. Must not affect session cleanup on any failure.

### `supabase_client.py`
Three additions:

1. `save_interview()` — adds `stage_notes jsonb` field to the insert
2. `save_eval_report(report: SessionEvalReport)` — persists to `eval_reports` table
3. `save_stage_eval_reports(reports: list[StageEvalReport], eval_report_id)` — persists to `stage_eval_reports` table

### `app.py`
One addition:

- `/api/eval-report/<session_id>` — GET endpoint serving the SessionEvalReport for a given session, rendered via `eval_report.html`

---

## Storage

```
eval_patches/
  {session_id_1}.json    ← SessionEvalReport per session (local backup)
  {session_id_2}.json

prompt_versions/
  v1.json                ← baseline prompt snapshot
  v2.json                ← after first patch applied
```

Both directories sit at project root. Neither is served publicly.
`eval_patches/` is gitignored if prompts are considered proprietary.

---

## Supabase Schema

```sql
CREATE TABLE eval_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interview_id uuid REFERENCES interviews(id) ON DELETE CASCADE,
    user_id uuid REFERENCES auth.users(id),
    session_eval jsonb NOT NULL,
    overall_agent_pass bool,
    overall_candidate_signal text,
    created_at timestamptz DEFAULT now()
);

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

CREATE INDEX idx_eval_reports_interview ON eval_reports(interview_id);
CREATE INDEX idx_stage_eval_interview ON stage_eval_reports(interview_id);
CREATE INDEX idx_stage_eval_type ON stage_eval_reports(stage_type);
```

---

## The Feedback Loop Step by Step

1. Session ends → conversation JSON + stage_notes saved to Supabase
2. Background task fires → `EvalPipeline.run()` processes each stage in order, carrying prior signals forward
3. `SessionEvalReport` written to `eval_patches/{session_id}.json` and persisted to Supabase
4. After N sessions (default 5), run `aggregator.py` manually → reads all patch files → identifies recurring failure patterns by stage type and dimension → outputs `AggregationReport` with proposed `PromptPatch` objects
5. Human reviews `AggregationReport` → approves or rejects each patch
6. Approved patches applied via `prompt_store.apply_patch()` → version logged
7. Next session runs with updated stage prompt templates → evaluator continues grading → cycle repeats

---

## Evaluator Isolation Constraints

The evaluator LLM call sees only:
- Raw dialogue turns (agent text + candidate text) for the specific stage
- Agent note for that stage
- Context bundle (resume, JD, role, experience level, company context)
- Prior stage signals
- Programmatic signals for that stage
- Stage rubric from `eval_rubrics.py`

It never sees:
- Tool call internals or function call logs
- FSM state or transition logic
- `assess_response` internal scores (these appear only as programmatic signals)
- Other stages' transcripts directly (only their signals, via prior_stage_signals)

Same model as the interview agent (GPT-4o-mini) is acceptable. Different system prompt
is mandatory. The evaluator system prompt must be tuned to be skeptical — it must
penalize leniency and reward specific, evidence-backed critique.

---

## What Does Not Change

- `fsm.py` — no changes
- `worker_manager.py` — no changes
- `speech_analytics.py` — no changes (output consumed by eval as programmatic signals)
- `document_processor.py` — no changes
- `audio_cache.py` — no changes
- `conversation_cache.py` — no changes
- `auth_helpers.py` — no changes
- Candidate-facing feedback (`POSTINTERVIEWFEEDBACK`, `FEEDBACKSCORES`) — no changes
- All LiveKit voice pipeline components — no changes
- All existing API endpoints except addition of `/api/eval-report/<session_id>`

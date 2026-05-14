# VoiceLoop — Layer 1 Post-Session Evaluator: Changes

## What This Adds

A decoupled post-session evaluator that reads the saved interview transcript after every session ends, grades the interview agent's conduct (not the candidate), and accumulates critique across sessions to propose targeted prompt rewrites. Nothing in the live voice pipeline changes.

---

## New Files

### `evaluator.py`
Standalone module. No LiveKit, no WebRTC, no voice.

- `evaluate_session(transcript_path: str) -> EvalReport` — reads the saved conversation JSON, calls a separate LLM with a skeptical evaluator system prompt, returns a structured `EvalReport` dataclass
- `EvalReport` — dataclass with fields: `session_id`, `timestamp`, `scores` (dict of dimension → 1–5), `failures` (list of specific transcript quotes with critique), `overall_pass: bool`
- Evaluator LLM call is isolated — sees only the raw dialogue (agent turns + candidate turns), never tool call internals or FSM state. This is mandatory.

Grading dimensions (scored 1–5, skeptical baseline, fails below 3):
- `follow_up_quality` — did the agent probe candidate-specific threads or ask the next generic question?
- `depth_score_accuracy` — did the agent's own `assess_response` scores match what the transcript actually shows?
- `transition_integrity` — were stage transitions earned or premature relative to minimum question gates?
- `thread_capture` — did the agent pick up on concrete signals the candidate dropped (project names, specific tools, challenges mentioned) and follow them?

### `aggregator.py`
Runs periodically (manually or on a cron). Not called during live sessions.

- `aggregate_patches(patch_dir: str, min_sessions: int = 5) -> AggregationReport` — reads all `EvalReport` JSON files in the patch directory, groups failures by dimension and recurring pattern, returns proposed prompt rewrites per affected stage
- `AggregationReport` — dataclass with fields: `sessions_analyzed`, `pattern_hits` (dict of pattern → count), `proposed_patches` (list of `PromptPatch`)
- `PromptPatch` — dataclass with fields: `stage`, `attribute` (which prompt attribute to rewrite: `conversation`, `rules`, `style`, `transition`), `current_text`, `proposed_text`, `reason`
- Does not apply patches automatically. Produces a human-reviewable report only.

### `prompt_store.py`
Versioned wrapper around the prompt system.

- `PromptStore` — loads current stage prompt attributes at init, tracks version history in a local JSON log
- `apply_patch(patch: PromptPatch) -> bool` — writes the proposed rewrite to the relevant stage prompt attribute, logs the change with timestamp and session IDs that triggered it
- `rollback(version: int)` — restores prompts to a previous version
- `get_current_version() -> int` — returns current version number
- Patches are applied manually after human review of the `AggregationReport`. There is no auto-apply.

---

## Modified Files

### `agent_worker.py`
One addition only — a post-session trigger at the end of the CLOSING stage cleanup.

After the conversation JSON is saved to Supabase (existing behavior), fire an async background task:

```python
asyncio.create_task(_run_post_session_eval(interview_id, transcript_path))
```

`_run_post_session_eval` is a thin async wrapper that calls `evaluator.evaluate_session()` and writes the resulting `EvalReport` to disk as `eval_patches/{session_id}.json`. It runs after the participant has disconnected. Any exception is caught and logged — it must never affect the session or the existing feedback flow.

### `app.py`
No changes required for Layer 1.

---

## Storage

```
eval_patches/
  {session_id_1}.json    ← EvalReport per session
  {session_id_2}.json
  ...

prompt_versions/
  v1.json                ← baseline prompts snapshot
  v2.json                ← after first patch applied
  ...
```

Both directories sit at project root. Neither is served publicly. `eval_patches/` is gitignored if prompts are considered proprietary.

---

## The Feedback Loop Step by Step

1. Session ends → conversation JSON saved (existing MockFlow behavior)
2. Background task fires → `evaluator.py` reads transcript → writes `EvalReport` to `eval_patches/`
3. After N sessions (default 5), run `aggregator.py` manually → reads all patch files → identifies recurring failure patterns across sessions → outputs `AggregationReport` with proposed prompt rewrites
4. Human reviews `AggregationReport` → approves or rejects each `PromptPatch`
5. Approved patches applied via `prompt_store.apply_patch()` → version logged
6. Next session runs with updated prompts → evaluator continues grading → cycle repeats

---

## What Does Not Change

- `fsm.py` — no changes
- `prompts.py` — only its content changes (via `prompt_store`), not its structure
- `worker_manager.py` — no changes
- `supabase_client.py` — no changes
- All LiveKit voice pipeline components — no changes
- Candidate-facing feedback (`POSTINTERVIEWFEEDBACK`, `FEEDBACKSCORES`) — no changes
- All existing API endpoints — no changes

---

## Constraints

- The evaluator LLM call must use a different system prompt from the interview agent. Same model is fine, different prompt is mandatory.
- The evaluator must never be given tool call logs, FSM state, or `assess_response` internal scores as input. Only the raw dialogue turns.
- `_run_post_session_eval` must be wrapped in try/except. A failed eval must not raise, must not affect session cleanup, and must log the error with the session ID for manual inspection.
- Patch application is always manual. No automatic prompt mutation.

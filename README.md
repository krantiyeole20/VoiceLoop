# VoiceLoop

Real-time voice AI mock interview platform with a post-session stage-bifurcated
self-evaluator loop.

> Build status: **Phase 1 (Foundation — Intro Call track)**.
> Read [INIT.md](INIT.md) for the full build plan.
> Reference implementation: `external-stuff/MockFlow-AI/` (do not deploy; reading only).

---

## What this is

- A voice mock-interview agent built on LiveKit Agents + Deepgram + OpenAI + Silero.
- Six interview tracks (Phase 2+): Intro Call, Behavioral, Technical SWE, DS/ML,
  Analytics/BI, Product/Strategy.
- An **agent note** written at every stage transition revealing the agent's intent.
- A **post-session evaluator** (Phase 3+) producing one report per stage with two
  faces: agent conduct + candidate performance.
- An **MCP server** (Phase 4+) exposing the evaluator for external consumption.

---

## Quick start (Phase 1 — Intro Call only)

### 1. Prereqs

- Python 3.11
- A Supabase project (fresh; see §5 of INIT.md for migration if reusing MockFlow's project)
- LiveKit Cloud account (or self-hosted LiveKit) — only required when actually running a session; BYOK keys are stored per-user
- OpenAI API key (for `generate_welcome_audio.py` server-side asset generation)
- Deepgram API key (only required at session time, also BYOK)

### 2. Install

```bash
git clone <this-repo> VoiceLoop
cd VoiceLoop
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.template .env
# Fill in SUPABASE_*, FERNET_KEY, FLASK_SECRET_KEY, GOOGLE_CLIENT_*, and OPENAI_API_KEY (server-side, for welcome-audio gen)
```

### 3. Database

In your Supabase project's SQL editor, paste and run the contents of
`supabase_schema.sql`.

Configure Google OAuth in **Supabase Dashboard → Authentication → Providers → Google**
with your `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and the callback URL
`http://localhost:5000/auth/callback` (or your prod URL).

### 4. Generate welcome audio

```bash
python generate_welcome_audio.py --track intro
# Phase 2 adds: --track all
```

This writes `static/audio/welcome_intro.mp3`. Commit the MP3 — they're build-time
assets, not runtime-generated.

### 5. Verify setup

```bash
python verify_setup.py
```

Should print all green checks. If something's red, fix the env or the schema before
running the server.

### 6. Run

```bash
python app.py
# In a second terminal, browse to http://localhost:5000
# Log in with Google → /api-keys → paste BYOK credentials → /start → run an interview.
```

---

## Project layout

See [INIT.md §2](INIT.md) for the complete file tree and per-file responsibilities.

```
VoiceLoop/
├── INIT.md                       Build plan
├── app.py                        Flask web server
├── agent_worker.py               LiveKit agent subprocess (one per session)
├── worker_manager.py             Subprocess lifecycle
├── fsm.py                        Generic interview state machine
├── stage_registry.py             Stage archetypes + track sequences
├── prompts.py                    Stage-type-keyed prompt templates
├── supabase_client.py            DB persistence + Fernet-encrypted BYOK key store
├── audio_cache.py                Pre-generated welcome audio
├── document_processor.py         PDF / DOCX / MD / TXT extraction for resume + JD
├── speech_analytics.py           Filler / WPM / pace analysis
├── conversation_cache.py         In-memory transcript cache
├── auth_helpers.py               Supabase auth decorator + helpers
├── postprocess.py                Transcript merging
├── evaluator.py                  Phase 3+: eval pipeline
├── eval_rubrics.py               Phase 3+: per-stage-type rubrics
├── aggregator.py                 Phase 4+: cross-session patch proposals
├── prompt_store.py               Phase 4+: versioned prompt mutation
├── eval_mcp_server.py            Phase 4+: MCP server
├── supabase_schema.sql           One-shot DDL + RLS
├── env.template                  Copy to .env
├── requirements.txt              Pinned deps
├── generate_welcome_audio.py     One-shot TTS generation script
├── verify_setup.py               Smoke test
├── templates/                    Jinja2 HTML
├── static/                       CSS, JS, MP3 audio
└── public/                       favicon
```

---

## Architecture highlights (read INIT.md for full detail)

- **Stage Collapse:** 12 `StageType` archetypes; each track is an ordered list of
  `StageConfig` instances parameterised at config time. One FSM. One state class.
- **Agent Note at transition:** `transition_stage(reason, agent_note)` — the agent
  populates a stage-type-specific schema. Schema failure is flagged in eval but
  never blocks the transition.
- **Eval pipeline:** Fires post-session as one `asyncio.create_task`. One LLM call
  per stage, with progressive disclosure (prior-stage signals carried forward).
  Outputs `StageEvalReport` with `agent_face` + `candidate_face`. Aggregated into
  `SessionEvalReport`.
- **BYOK:** Each user provides LiveKit, OpenAI, Deepgram keys (Fernet-encrypted in
  Supabase). Each session spawns a subprocess with those keys injected via env.
- **Voice correctness:** VAD/turn-detection/interruption/closing/fallback patterns
  are carried over verbatim from MockFlow-AI. See INIT.md §9 for the
  non-negotiable list.

---

## License

See `LICENSE`.

# MockFlow-AI → VoiceLoop: Architecture Research & Feature Map

> **Scope** — Your workspace (`/Users/krantiy/Documents/VoiceLoop/`) currently contains only a
> `README.md` (3 lines) and a `.gitignore`. All code that exists lives in
> `external-stuff/MockFlow-AI/`. Every citation below points to **real, verified code** in that
> reference implementation — nothing is theoretical.

---

## MockFlow Core Abstractions (from README + source)

MockFlow-AI is built on three interlocking abstractions:

| Abstraction | Description | Primary File |
|---|---|---|
| **Multi-Track FSM** | Explicit stage-enum state machine; one per track type. No LLM decides transitions — code does. | [fsm.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py) |
| **Tool-Calling Agent** | LiveKit `Agent` subclass; LLM calls typed `@function_tool` methods to advance FSM, ask questions, record responses. | [agent_worker.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py) |
| **Per-Session Subprocess Worker** | Flask spawns a dedicated `agent_worker.py` subprocess per interview room, passing BYOK keys via env. | [worker_manager.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/worker_manager.py) |

---

## Tier 1 — Core Architecture (Engine / Agent Logic)

### 1.1 · Finite State Machine (FSM)

The FSM is the backbone. Stage transitions are **explicit, timestamp-tracked, code-enforced** — the LLM
cannot silently skip stages.

- **`InterviewState` dataclass** — mutable per-session state: current stage, timestamps, question counts,
  document slots, pending transitions, skip queue.
  → [fsm.py:56–113](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L56-L113)

- **`InterviewStage` enum** — 5-stage Intro track: `WELCOME → SELF_INTRO → PAST_EXPERIENCE → COMPANY_FIT → CLOSING`.
  → [fsm.py:19–25](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L19-L25)

- **`STAGE_TIME_LIMITS` / `STAGE_MIN_QUESTIONS`** — Centralized time and depth gates per stage
  (e.g. PAST_EXPERIENCE: 240 s, min 5 questions).
  → [fsm.py:29–44](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L29-L44)

- **`transition_to()`** — Explicit state change with forced/skipped flags, counter increments,
  and timestamp reset. Called by agent, never by LLM directly.
  → [fsm.py:114–150](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L114-L150)

- **`get_progress_summary()`** — Injects urgency-annotated progress string (`[PROGRESS] Stage: ... | Urgency: CRITICAL`)
  into agent context.
  → [fsm.py:352–382](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L352-L382)

- **`get_document_context()`** — Stage-gated RAG: resume injected only at PAST_EXPERIENCE;
  JD only at COMPANY_FIT.
  → [fsm.py:396–443](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L396-L443)

#### Branch: Multi-Track FSM Extensions

Each additional interview track gets its **own stage enum + state subclass**:

| Track | Stage Enum | State Class | Stages |
|---|---|---|---|
| Behavioral | `BehavioralStage` | `BehavioralInterviewState` | `GREETING → SELF_INTRO → BEHAVIORAL_Q1/2/3 → CLOSING` |
| Technical Voice | `TechnicalVoiceStage` | `TechnicalVoiceInterviewState` | `GREETING → SELF_INTRO → EXPERIENCE_DISCUSSION → TECHNICAL_CONCEPTS_1/2/3 → CLOSING` |
| Technical Coding | `CodingStage` | `CodingInterviewState` | `GREETING → SELF_INTRO → WARM_UP → CODING_PROBLEM_1/2 → CLOSING` |

→ [fsm.py:479–508](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L479-L508)
→ [fsm.py:551–715](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L551-L715) (BehavioralInterviewState)
→ [fsm.py:718–794](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L718-L794) (TechnicalVoiceInterviewState)

---

### 1.2 · Interview Agent & Tool Orchestration

`InterviewAgent(Agent)` is a LiveKit `Agent` subclass. The LLM calls **typed `@function_tool` methods**
rather than free-form output. Tools advance the FSM and control flow:

| Tool | Purpose |
|---|---|
| `transition_stage()` | Advance FSM to next stage; enforces min-time gates; emits WebRTC stage_change event; updates instructions. |
| `ask_question()` | Deduplication gate — rejects repeated questions, tracks counts, injects pending transition ACK. |
| `assess_response()` | Scores candidate depth (1–5), emits urgency/transition guidance back to LLM. |
| `generate_interview_questions()` | Single LLM call at session start to generate behavioral/technical/coding questions from resume+JD. |
| `get_current_question()` | Returns the question for the current stage index; for coding, also emits problem to frontend via data channel. |
| `record_response()` | Persists candidate response summary to state list. |
| `evaluate_code_submission()` | Calls `CODE_EVALUATOR` prompt, scores code, emits result to frontend, persists to Supabase. |
| `skip_coding_problem()` | Marks problem skipped; instructs agent to call `transition_stage`. |

→ [agent_worker.py:106–761](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L106-L761)

- **`_get_stage_instructions()`** — Builds personalized system prompt per stage: injects behavioral question
  template variables (`{question_text}`, `{competency}`), technical topic variables,
  document context, role context, and personality note.
  → [agent_worker.py:214–278](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L214-L278)

- **`_emit_stage_change()`** — Publishes JSON `{type: "stage_change", stage: "..."}` via LiveKit
  `publish_data` for real-time UI updates.
  → [agent_worker.py:280–295](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L280-L295)

- **`on_enter()` / `on_exit()`** — Lifecycle hooks: plays cached welcome audio or falls back to LLM TTS.
  → [agent_worker.py:762–786](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L762-L786)

---

### 1.3 · Per-Session Subprocess Worker (BYOK Isolation)

Each interview spawns a **dedicated `agent_worker.py` subprocess** with BYOK keys injected via env.
No shared process — keys are ephemerally isolated.

- **`WorkerManager.spawn_worker()`** — Launches `python agent_worker.py` as subprocess; forwards logs
  via daemon thread; waits ≥ 8 s for Silero VAD model load.
  → [worker_manager.py:51–119](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/worker_manager.py#L51-L119)

- **`_wait_for_worker_ready()`** — Polls process liveness; assumes ready after 8 s if still alive.
  → [worker_manager.py:121–161](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/worker_manager.py#L121-L161)

- **`cleanup_all_workers()`** — `atexit` hook terminates all active workers on server shutdown.
  → [worker_manager.py:189–196](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/worker_manager.py#L189-L196)

---

### 1.4 · Prompt System

All prompts are class constants, not strings scattered in code. `build_stage_instructions()` assembles
them by combining modular class attributes.

- **Stage prompt classes** (`WELCOME`, `SELF_INTRO`, `PAST_EXPERIENCE`, `COMPANY_FIT`, `CLOSING`)
  — each has `.conversation`, `.style`, `.rules`, `.transition` attributes.
  → [prompts.py:13–244](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/prompts.py#L13-L244)

- **`TRANSITION_ACKS` / `FALLBACK_ACKS`** — Named per-stage acknowledgement strings with
  `[CANDIDATE_NAME]` / `[ROLE]` template slots.
  → [prompts.py:250–273](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/prompts.py#L250-L273)

- **`ROLE_CONTEXT` / `PERSONALITY`** — Role-keyword → focus-area mapping; experience level → expectation
  mapping; combined into a per-session personality note injected into every system prompt.
  → [prompts.py:294–338](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/prompts.py#L294-L338)

- **`QUESTION_GENERATION`** (behavioral / technical / coding / topic extraction system prompts)
  — LLM-generated structured JSON questions/problems at session start.
  *(defined later in prompts.py)*

- **`CODE_EVALUATOR`** — Separate system prompt for code evaluation; returns JSON with
  `correctness`, `approach_quality`, `brief_verbal_feedback`.
  *(defined later in prompts.py)*

- **`build_stage_instructions()`** — Router that assembles the right combination of prompt parts
  for each stage enum value, including all 4 tracks.
  → [prompts.py:586–683](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/prompts.py#L586-L683)

---

### 1.5 · Voice Pipeline

Built on LiveKit Agents SDK with three plugged-in components:

| Component | Provider | Purpose |
|---|---|---|
| STT | Deepgram Nova-2 | Continuous speech-to-text |
| LLM | OpenAI GPT-4o-mini | Adaptive, context-aware responses |
| TTS | OpenAI TTS | Natural voice synthesis |
| VAD | Silero | Turn-taking / voice activity detection |

→ [agent_worker.py:17–26](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L17-L26) (plugin imports)

---

## Tier 2 — Functional Features (User-Facing Capabilities)

### 2.1 · Four Interview Tracks

Each track is a self-contained configuration in `tracks/`:

| Track | Config File | Key Settings |
|---|---|---|
| Intro Call | [intro.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/tracks/intro.py) | 5-stage, general background |
| Behavioral | [behavioral.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/tracks/behavioral.py) | STAR framework, 2–3 questions, depth setting |
| Technical Voice | [technical_voice.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/tracks/technical_voice.py) | 1–3 topic concepts, no coding |
| Technical Coding | [technical_coding.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/tracks/technical_coding.py) | Monaco editor, 1–2 LLM problems, 15-min timer, 3 attempts |

→ [tracks/__init__.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/tracks/__init__.py) — `get_track_config()` dispatcher
→ [tracks/base.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/tracks/base.py) — Base track config dataclass

---

### 2.2 · Resume / JD Document Upload & RAG Injection

- **`DocumentProcessor`** — Extracts text from PDF (PyPDF2), DOCX (python-docx), MD, TXT. Caches by MD5 hash. Privacy-first: never stores raw files.
  → [document_processor.py:31–410](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/document_processor.py#L31-L410)

- **`/api/upload-resume`** (Flask endpoint) — Accepts multipart file, extracts text, returns `cache_key`.
  → [app.py:630–720](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L630-L720)

- **Stage-gated injection** — `get_document_context(stage)` in state class controls which document
  gets injected at which stage; resume at PAST_EXPERIENCE, JD at COMPANY_FIT/behavioral question stages.
  → [fsm.py:396–443](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/fsm.py#L396-L443)

- **Topic auto-extraction** — `/api/extract-topics` calls GPT-4o-mini to pull tech topics from resume
  for Technical Voice track suggestion.
  → [app.py:500–547](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L500-L547)

---

### 2.3 · Speech Analytics

Pure functions (no I/O). Called post-interview on the conversation dict.

- **Filler word detection** — regex whole-word match for 13 fillers (`um`, `uh`, `like`, `basically`, etc.).
  → [speech_analytics.py:16–82](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/speech_analytics.py#L16-L82)

- **WPM calculation** — Total words / estimated speaking duration (gaps > 5 s excluded).
  → [speech_analytics.py:85–110](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/speech_analytics.py#L85-L110)

- **Per-turn pace** — WPM per turn (first 20), returned as list for frontend charting.
  → [speech_analytics.py:113–121](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/speech_analytics.py#L113-L121)

---

### 2.4 · Post-Interview Feedback System

Two-stage LLM feedback pipeline:

- **`FEEDBACKSCORES`** — First call: structured JSON extraction (`overall_score`, 3–5 competencies,
  `filler_word_count`, `answer_structure_score`). Enables fast visual display before full text loads.
  → [prompts.py:513–579](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/prompts.py#L513-L579)

- **`POSTINTERVIEWFEEDBACK`** — Second call: full competency-based, evidence-backed feedback with
  micro-techniques, before/after answer rewrites, 2-week practice plan.
  → [prompts.py:342–505](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/prompts.py#L342-L505)

- **`/api/feedback`** (Flask endpoint) — Assembles transcript + candidate profile; calls both LLM stages;
  persists result to Supabase; serves feedback page.
  *(defined in app.py after line 800)*

---

### 2.5 · Adaptive Question & Problem Generation

- **Behavioral** — GPT-4o-mini generates `count` STAR questions with `main_question`, `competency`,
  `follow_up_probes` in JSON; tailored to framework (Amazon/Google/Meta/Generic) + resume + JD.
  → [agent_worker.py:435–466](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L435-L466)

- **Technical Voice** — One GPT call per topic; generates ordered question list per topic.
  → [agent_worker.py:468–495](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L468-L495)

- **Coding** — Generates 1–2 `problems` with `title`, `description`, `examples`, `constraints`,
  `time_limit_minutes`; difficulty auto-adjusted from experience level.
  → [agent_worker.py:497–533](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L497-L533)

---

### 2.6 · Live Code Evaluation (Coding Track)

- **`evaluate_code_submission()`** — Calls `CODE_EVALUATOR` prompt with problem + submitted code;
  returns JSON evaluation (`correctness`, `approach_quality`, `brief_verbal_feedback`);
  allows up to 3 attempts per problem.
  → [agent_worker.py:643–738](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L643-L738)

- **Problem emission via WebRTC data channel** — Problem JSON sent to frontend Monaco editor via
  `room.local_participant.publish_data()`.
  → [agent_worker.py:590–607](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L590-L607)

- **Coding submission persistence** — `save_coding_submission()` writes attempt to `coding_submissions`
  Supabase table (with RLS).
  → [supabase_client.py:280–323](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/supabase_client.py#L280-L323)

---

### 2.7 · BYOK (Bring Your Own Keys) Architecture

- **Encrypted key storage** — Fernet symmetric encryption; keys stored encrypted in Supabase
  `user_api_keys` table; never logged.
  → [supabase_client.py:24–121](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/supabase_client.py#L24-L121)

- **Key validation endpoint** — `/api/user/keys/validate` checks format before saving.
  → [app.py:223–253](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L223-L253)

- **Ephemeral key injection** — Keys loaded from Supabase at token-request time and injected only into
  the subprocess environment; not persisted in memory beyond session.
  → [app.py:387–422](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L387-L422)

---

### 2.8 · Session Persistence & Interview History

- **`save_interview()`** — Persists full conversation, metadata, track config, skipped stages to
  `interviews` Supabase table; returns `interview_id`.
  → [supabase_client.py:123–220](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/supabase_client.py#L123-L220)

- **`get_user_interviews()`** — Fetches paginated interview history (newest-first).
  → [supabase_client.py:222–229](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/supabase_client.py#L222-L229)

- **Conversation cache** — In-process `conversation_cache` with `ConversationMetadata` for mid-session
  feedback access before Supabase persistence.
  → [app.py:725–792](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L725-L792)
  → [conversation_cache.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/conversation_cache.py)

---

### 2.9 · Pre-Generated Welcome Audio Cache

- **`WELCOME_AUDIO_FILES` / `WELCOME_SCRIPTS`** — Per-track static audio files served at session
  start to avoid TTS latency on first turn. Falls back to live TTS if files missing.
  → [audio_cache.py:18–49](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/audio_cache.py#L18-L49)

- **`get_welcome_audio_bytes()`** — Returns MP3 bytes from `static/audio/welcome_{track}.mp3`;
  returns `None` for TTS fallback.
  → [audio_cache.py:52–80](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/audio_cache.py#L52-L80)

- **`generate_and_cache_welcome_audio()`** — One-time setup: calls OpenAI TTS API and writes to disk.
  → [audio_cache.py:97–136](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/audio_cache.py#L97-L136)

---

## Tier 3 — UI / Integration Layer (Presentation)

### 3.1 · Flask Web Server & Auth

- **Google OAuth via Supabase** — `/auth/login` → Supabase OAuth → `/auth/callback` (HTML page
  extracts token from URL fragment via JS) → `/auth/session` sets Flask session.
  → [app.py:74–162](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L74-L162)

- **`@require_auth` decorator** — Route guard for all protected endpoints.
  → [auth_helpers.py](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/auth_helpers.py)

- **Page routes** — `/ → /dashboard → /start → /interview → /past-calls → /feedback/<id>`
  → [app.py:272–349](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L272-L349)

---

### 3.2 · LiveKit Token Generation & Worker Spawning

- **`/api/token`** — Single endpoint that: (1) fetches BYOK keys, (2) spawns `agent_worker.py` subprocess,
  (3) waits for readiness, (4) generates LiveKit JWT with participant attributes
  (track, framework, depth, topics, resume, JD).
  → [app.py:354–497](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L354-L497)

---

### 3.3 · Real-Time UI Events via WebRTC Data Channel

All UI updates are driven by JSON messages published by the agent over LiveKit's data channel:

| Event `type` | Payload | Trigger |
|---|---|---|
| `stage_change` | `{stage: string}` | Every `transition_stage()` call |
| `coding_problem` | `{problem, problem_index, attempt_number, time_limit_minutes}` | `get_current_question()` for coding |
| `evaluation_result` | `{evaluation, attempt, max_attempts, problem_index}` | `evaluate_code_submission()` |
| `user_caption` | `{text: string}` | Real-time STT caption emission |

→ [agent_worker.py:280–295](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L280-L295) (stage_change)
→ [agent_worker.py:590–607](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L590-L607) (coding_problem)
→ [agent_worker.py:693–706](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L693-L706) (evaluation_result)
→ [agent_worker.py:789–796](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/agent_worker.py#L789-L796) (user_caption)

---

### 3.4 · HTML Templates

Six Jinja2 templates served by Flask:

| Template | Route | Purpose |
|---|---|---|
| `index.html` | `/` | Landing page |
| `form.html` | `/start` | Interview configuration (track, framework, depth, topics, file upload) |
| `interview.html` | `/interview` | Interview room (LiveKit JS SDK, Monaco editor for coding) |
| `dashboard.html` | `/dashboard` | User home with past interviews |
| `past_calls.html` | `/past-calls` | Interview history list |
| `feedback.html` | `/feedback/<id>` | Feedback viewer with competency scores |
| `auth_callback.html` | `/auth/callback` | OAuth token extraction from URL fragment |
| `api_keys.html` | `/api-keys` | BYOK key management |

→ [app.py:272–349](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/app.py#L272-L349)
→ [templates/](file:///Users/krantiy/Documents/VoiceLoop/external-stuff/MockFlow-AI/templates/)

---

## VoiceLoop Project Gap Analysis

> [!IMPORTANT]
> VoiceLoop's own codebase is currently a blank slate — only a README and `.gitignore` exist.
> The feature list above represents **what MockFlow-AI has implemented** and what VoiceLoop
> would need to build from scratch (or adapt from the reference implementation).

### What VoiceLoop Has Today

| File | Content |
|---|---|
| [README.md](file:///Users/krantiy/Documents/VoiceLoop/VoiceLoop/README.md) | "VoiceLoop — Real-time voice-ai interview platform with self-eval+update loop" (3 lines) |
| [.gitignore](file:///Users/krantiy/Documents/VoiceLoop/.gitignore) | `external-stuff/` exclusion |

### What MockFlow Adds That VoiceLoop Doesn't Have Yet

Every item in Tiers 1–3 above is **absent** from VoiceLoop's own codebase. Key differentiators
to design for VoiceLoop (per its tagline "self-eval+update loop"):

| VoiceLoop Concept | MockFlow Closest Analog | Gap / Design Opportunity |
|---|---|---|
| **Self-evaluation loop** | `assess_response()` tool (scores depth 1–5) | MockFlow only guides the *agent*; VoiceLoop could expose this score to the *candidate* in real-time |
| **Update loop** | Stage instructions rebuild via `update_instructions()` | VoiceLoop could make the LLM instructions themselves evolve based on cumulative performance |
| **Real-time voice** | LiveKit STT→LLM→TTS pipeline | Identical requirement — MockFlow's pipeline is production-ready |
| **FSM** | `InterviewState` + `transition_to()` | Core abstraction to adopt verbatim or extend |
| **Feedback** | `POSTINTERVIEWFEEDBACK` + `FEEDBACKSCORES` | Can be lifted directly; extend with self-directed practice scheduling |

---

*Generated by Antigravity research on 2026-05-09. All citations verified against live source files in
`external-stuff/MockFlow-AI/`.*

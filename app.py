"""
VoiceLoop — Flask web server

Adapted from MockFlow. What's different in Phase 1:
  * /api/token validates `track` against StageRegistry.is_implemented(); only
    `intro` is implemented in Phase 1.
  * Coding-track endpoints removed.
  * /api/eval-report/<interview_id> route is wired but returns a Phase-1 stub.
  * Form posts the same camelCase keys as MockFlow for backward compat.

Everything else (auth, BYOK, /api/upload-resume, /api/interview/<id>,
/api/feedback/*, /past-calls, etc.) carries over identically.
"""

from __future__ import annotations

import atexit
import logging
import os
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_cors import CORS

# Load .env BEFORE importing modules that read env at import time
# (supabase_client, auth_helpers).
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from livekit import api as livekit_api  # noqa: E402

from auth_helpers import (  # noqa: E402
    get_current_user,
    get_user_id,
    is_authenticated,
    require_auth,
)
from conversation_cache import ConversationMetadata, conversation_cache  # noqa: E402
from document_processor import DocumentMetadata, doc_processor  # noqa: E402
from postprocess import (  # noqa: E402
    get_interview_summary,
    list_interviews,
    merge_by_agent_turns,
)
from stage_registry import StageRegistry, TrackType  # noqa: E402
from supabase_client import supabase_client  # noqa: E402
from worker_manager import worker_manager  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = (
    os.getenv("FLASK_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or "dev-secret-key-change-in-prod"
)
CORS(app)

# Ensure spawned workers don't outlive the parent process.
atexit.register(worker_manager.cleanup_all_workers)


# ===========================================================================
# Env validation (warn-only in dev; hard fail in production)
# ===========================================================================

REQUIRED_ENV_VARS = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_ANON_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
]
_missing_required = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
if not (os.getenv("FERNET_KEY") or os.getenv("ENCRYPTION_KEY")):
    _missing_required.append("FERNET_KEY (or legacy ENCRYPTION_KEY)")
if _missing_required:
    msg = f"[CONFIG] Missing required env vars: {', '.join(_missing_required)}"
    logger.error(msg)
    if os.getenv("FLASK_ENV") == "production":
        raise ValueError(msg)
    else:
        logger.warning("[CONFIG] Continuing in dev mode despite missing env vars")
else:
    logger.info("[CONFIG] All required env vars validated")
logger.info("[CONFIG] BYOK model: LiveKit, OpenAI, and Deepgram keys are loaded from the user database")


# ===========================================================================
# AUTH ENDPOINTS
# ===========================================================================

@app.route("/auth/login")
def login():
    try:
        redirect_url = f"{request.host_url}auth/callback"
        auth_url = (
            f"{os.getenv('SUPABASE_URL')}/auth/v1/authorize?provider=google"
            f"&redirect_to={redirect_url}"
        )
        logger.info(f"[AUTH] Redirecting to OAuth: {auth_url}")
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"[AUTH] Login error: {e}")
        return "Login failed", 500


@app.route("/auth/callback")
def auth_callback():
    """Supabase returns tokens in the URL fragment; the template extracts them via JS."""
    logger.info(f"[AUTH] Callback received — URL: {request.url}")
    return render_template("auth_callback.html")


@app.route("/auth/session", methods=["POST"])
def set_session():
    try:
        data = request.json or {}
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        if not access_token:
            return jsonify({"error": "No access token"}), 400
        session["access_token"] = access_token
        if refresh_token:
            session["refresh_token"] = refresh_token
        logger.info("[AUTH] User session set")
        return jsonify({"success": True, "redirect": url_for("dashboard")})
    except Exception as e:
        logger.error(f"[AUTH] set_session failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/auth/logout")
def logout():
    session.clear()
    logger.info("[AUTH] User logged out")
    return redirect(url_for("index"))


@app.route("/api/auth/status")
def auth_status():
    try:
        user = get_current_user()
        if user:
            return jsonify({
                "authenticated": True,
                "user": {
                    "id": user.user.id,
                    "email": user.user.email,
                    "name": user.user.user_metadata.get("full_name"),
                    "avatar": user.user.user_metadata.get("avatar_url"),
                },
            })
        return jsonify({"authenticated": False})
    except Exception as e:
        logger.error(f"[AUTH] auth_status failed: {e}", exc_info=True)
        return jsonify({"authenticated": False})


# ===========================================================================
# BYOK KEYS ENDPOINTS
# ===========================================================================

@app.route("/api/user/keys/status")
@require_auth
def get_keys_status():
    try:
        user_id = get_user_id()
        keys = supabase_client.get_api_keys(user_id)
        if keys:
            lk = keys["livekit_url"]
            return jsonify({
                "has_keys": True,
                "livekit_url_masked": f"wss://{lk.split('//')[1][:15]}..." if "//" in lk else lk[:20],
                "livekit_key_masked": f"{keys['livekit_api_key'][:8]}...",
                "openai_masked":      f"sk-...{keys['openai_key'][-4:]}",
                "deepgram_masked":    f"...{keys['deepgram_key'][-4:]}",
            })
        return jsonify({"has_keys": False})
    except Exception as e:
        logger.error(f"[API] get_keys_status failed: {e}", exc_info=True)
        return jsonify({"has_keys": False})


@app.route("/api/user/keys", methods=["POST"])
@require_auth
def save_user_keys():
    try:
        user_id = get_user_id()
        data = request.json or {}
        required = ["livekit_url", "livekit_api_key", "livekit_api_secret", "openai_key", "deepgram_key"]
        if not all(data.get(k) for k in required):
            return jsonify({"error": "All API keys required"}), 400
        ok = supabase_client.save_api_keys(
            user_id,
            data["livekit_url"], data["livekit_api_key"], data["livekit_api_secret"],
            data["openai_key"], data["deepgram_key"],
        )
        if ok:
            logger.info(f"[API] API keys saved for user {user_id}")
            return jsonify({"success": True, "message": "Keys saved successfully"})
        return jsonify({"error": "Failed to save keys"}), 500
    except Exception as e:
        logger.error(f"[API] save_user_keys failed: {e}", exc_info=True)
        return jsonify({"error": "Internal error"}), 500


@app.route("/api/user/keys/validate", methods=["POST"])
@require_auth
def validate_keys():
    try:
        d = request.json or {}
        lk_url = d.get("livekit_url", "")
        if not (lk_url.startswith("wss://") or lk_url.startswith("ws://")):
            return jsonify({"valid": False, "message": "Invalid LiveKit URL format"})
        if not d.get("livekit_api_key") or len(d.get("livekit_api_key", "")) < 5:
            return jsonify({"valid": False, "message": "Invalid LiveKit API Key"})
        if not d.get("livekit_api_secret") or len(d.get("livekit_api_secret", "")) < 10:
            return jsonify({"valid": False, "message": "Invalid LiveKit API Secret"})
        if not d.get("openai_key", "").startswith("sk-"):
            return jsonify({"valid": False, "message": "Invalid OpenAI key format"})
        if len(d.get("deepgram_key", "")) < 10:
            return jsonify({"valid": False, "message": "Invalid Deepgram key format"})
        return jsonify({"valid": True})
    except Exception as e:
        logger.error(f"[API] validate_keys failed: {e}", exc_info=True)
        return jsonify({"valid": False, "message": "Validation error"}), 500


# ===========================================================================
# STATIC / PAGES
# ===========================================================================

@app.route("/favicon.ico")
def favicon():
    try:
        return send_from_directory(
            os.path.join(app.root_path, "public"),
            "favicon.ico",
            mimetype="image/x-icon",
        )
    except Exception:
        return ("", 404)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
@require_auth
def dashboard():
    return render_template("dashboard.html")


@app.route("/api-keys")
@require_auth
def api_keys_page():
    return render_template("api_keys.html")


@app.route("/start")
def start_form():
    return render_template("form.html")


@app.route("/interview")
def interview():
    return render_template(
        "interview.html",
        name=request.args.get("name", "Candidate"),
        email=request.args.get("email", ""),
        role=request.args.get("role", ""),
        level=request.args.get("level", ""),
    )


@app.route("/past-calls")
def past_calls():
    return render_template("past_calls.html")


@app.route("/past_calls.html")
def past_calls_alias():
    return render_template("past_calls.html")


@app.route("/history")
def history_redirect():
    return redirect(url_for("past_calls"))


@app.route("/feedback/<filename>")
def feedback_page(filename):
    return render_template("feedback.html", filename=filename)


@app.route("/eval-report/<interview_id>")
@require_auth
def eval_report_page(interview_id):
    """Phase 3+ will render templates/eval_report.html. Phase 1 stub."""
    return (
        f"<h1>Eval report</h1>"
        f"<p>Phase 1 stub. Eval pipeline runs in Phase 3.</p>"
        f"<p>interview_id={interview_id}</p>",
        200,
    )


# ===========================================================================
# TOKEN API — spawn worker + return LiveKit JWT
# ===========================================================================

@app.route("/api/token", methods=["POST"])
@require_auth
def generate_token():
    try:
        user_id = get_user_id()
        data = request.json or {}

        name              = data.get("name", "Anonymous")
        email             = data.get("email", "")
        role              = data.get("role", "")
        level             = data.get("level", "")
        resume_cache_key  = data.get("resumeCacheKey", "")
        job_description   = data.get("jobDescription", "")
        include_profile   = data.get("includeProfile", True)
        track             = data.get("track", "intro")
        company_name      = data.get("companyName", "")

        # ---- Validate track against the registry ----
        try:
            track_enum = TrackType(track)
        except ValueError:
            return jsonify({
                "error": "Invalid track",
                "message": f"Track '{track}' is not recognised. Valid: {[t.value for t in TrackType]}",
            }), 400

        if not StageRegistry.is_implemented(track_enum):
            implemented = [t.value for t in StageRegistry.implemented_tracks()]
            return jsonify({
                "error": "Track not yet available",
                "message": (
                    f"Track '{track}' is not yet implemented in this phase. "
                    f"Available: {implemented}"
                ),
            }), 400

        logger.info(f"[TOKEN] Token request from user {user_id} ({name}, track={track})")

        # ---- Fetch BYOK keys ----
        keys = supabase_client.get_api_keys(user_id)
        if not keys:
            return jsonify({
                "error": "API keys not configured",
                "message": "Please configure your API keys in Settings before starting an interview.",
            }), 400
        required_keys = ["livekit_url", "livekit_api_key", "livekit_api_secret", "openai_key", "deepgram_key"]
        missing = [k for k in required_keys if not keys.get(k)]
        if missing:
            return jsonify({
                "error": "Incomplete API keys",
                "message": f'Missing keys: {", ".join(missing)}',
            }), 400

        # ---- Spawn worker ----
        timestamp = int(time.time())
        safe_name = (name or "anon").lower().replace(" ", "-")
        room_name = f"interview-{safe_name}-{timestamp}-{uuid.uuid4().hex[:6]}"

        logger.info(f"[TOKEN] Spawning worker for room: {room_name}")
        worker_started = worker_manager.spawn_worker(
            room_name=room_name,
            livekit_url=keys["livekit_url"],
            livekit_api_key=keys["livekit_api_key"],
            livekit_api_secret=keys["livekit_api_secret"],
            openai_api_key=keys["openai_key"],
            deepgram_api_key=keys["deepgram_key"],
        )
        if not worker_started:
            return jsonify({
                "error": "Worker startup failed",
                "message": "Failed to start interview agent. Please try again.",
            }), 500
        logger.info(f"[TOKEN] Worker ready for room: {room_name}")

        # ---- Build participant attributes ----
        attributes = {
            "user_id": user_id,
            "role": role,
            "level": level,
            "email": email,
            "company_name": company_name,
            "include_profile": str(include_profile).lower(),
            "track": track,
        }
        if resume_cache_key:
            resume_text = doc_processor.get_cached_text(resume_cache_key)
            if resume_text:
                attributes["resume_text"] = resume_text[:3000]
                logger.info(f"[TOKEN] Attached resume text ({len(resume_text)} chars)")
        if job_description:
            attributes["job_description"] = job_description[:2000]
            logger.info(f"[TOKEN] Attached job description ({len(job_description)} chars)")

        # ---- Mint LiveKit token with the USER's keys (BYOK) ----
        token = livekit_api.AccessToken(keys["livekit_api_key"], keys["livekit_api_secret"])
        token.with_identity(name).with_name(name).with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        ).with_attributes(attributes)
        jwt_token = token.to_jwt()

        logger.info(f"[TOKEN] Token generated for room: {room_name}")
        return jsonify({
            "token": jwt_token,
            "url":   keys["livekit_url"],
            "room":  room_name,
            "candidate": {
                "name": name,
                "email": email,
                "role": role,
                "level": level,
            },
        })

    except Exception as e:
        logger.error(f"[TOKEN] Token generation error: {e}", exc_info=True)
        return jsonify({"error": "Token generation failed", "message": str(e)}), 500


@app.route("/api/worker/status/<room_name>")
@require_auth
def worker_status(room_name):
    try:
        status = worker_manager.get_worker_status(room_name)
        return jsonify({"room_name": room_name, "status": status or "not_found"})
    except Exception as e:
        logger.error(f"[WORKER] status check error: {e}")
        return jsonify({"error": str(e)}), 500


# ===========================================================================
# DOCUMENT UPLOAD API
# ===========================================================================

@app.route("/api/upload-resume", methods=["POST"])
def upload_resume():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file provided", "message": "Please upload a file"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "No file selected", "message": "Please select a file"}), 400

        document_type = request.form.get("document_type", "resume")
        if document_type not in ("resume", "job_description", "portfolio"):
            document_type = "resume"

        text = doc_processor.extract_text(f, filename=f.filename)
        if not text or text.startswith("["):
            return jsonify({
                "error": "Extraction failed",
                "message": text or "Could not extract text from file",
            }), 400

        f.seek(0, 2)
        file_size = f.tell()
        f.seek(0)

        metadata = DocumentMetadata(
            filename=f.filename,
            document_type=document_type,
            uploaded_at=time.time(),
            file_size=file_size,
            extraction_method="auto",
            char_count=len(text),
        )
        cache_key = doc_processor.cache_document(text, metadata)
        logger.info(f"[API] Document cached: {cache_key} ({len(text)} chars from {f.filename})")
        return jsonify({
            "success": True,
            "cache_key": cache_key,
            "filename": f.filename,
            "document_type": document_type,
            "char_count": len(text),
            "text_preview": text[:500] + ("..." if len(text) > 500 else ""),
        })
    except Exception as e:
        logger.error(f"[API] Upload error: {e}", exc_info=True)
        return jsonify({"error": "Upload failed", "message": str(e)}), 500


# ===========================================================================
# CONVERSATION CACHE API
# ===========================================================================

@app.route("/api/conversation/cache", methods=["POST"])
def cache_conversation():
    try:
        data = request.json or {}
        if not data.get("conversation"):
            return jsonify({"error": "No conversation provided"}), 400
        from datetime import datetime as _dt
        metadata = ConversationMetadata(
            candidate_name=data.get("candidate_name", "Unknown"),
            interview_date=_dt.now().isoformat(),
            room_name=data.get("room_name", ""),
            job_role=data.get("job_role", ""),
            experience_level=data.get("experience_level", ""),
            final_stage=data.get("final_stage", ""),
            ended_by=data.get("ended_by", "unknown"),
            skipped_stages=data.get("skipped_stages", []),
            has_resume=data.get("has_resume", False),
            has_jd=data.get("has_jd", False),
        )
        cache_key = conversation_cache.cache_conversation(data["conversation"], metadata)
        if not cache_key:
            return jsonify({"error": "Cache failed"}), 500
        return jsonify({"success": True, "cache_key": cache_key})
    except Exception as e:
        logger.error(f"[API] cache_conversation failed: {e}", exc_info=True)
        return jsonify({"error": "Cache failed", "message": str(e)}), 500


@app.route("/api/conversation/<cache_key>")
def get_cached_conversation(cache_key):
    try:
        conv = conversation_cache.get_conversation(cache_key)
        if not conv:
            return jsonify({"error": "Conversation not found"}), 404
        return jsonify({"success": True, "conversation": conv})
    except Exception as e:
        logger.error(f"[API] get_cached_conversation failed: {e}", exc_info=True)
        return jsonify({"error": "Failed to get conversation", "message": str(e)}), 500


# ===========================================================================
# INTERVIEW HISTORY API
# ===========================================================================

@app.route("/api/interviews")
def get_interviews():
    try:
        interviews = list_interviews()
        return jsonify({"success": True, "interviews": interviews, "count": len(interviews)})
    except Exception as e:
        logger.error(f"[API] list_interviews error: {e}", exc_info=True)
        return jsonify({"error": "Failed to list interviews", "message": str(e)}), 500


@app.route("/api/user/interviews")
@require_auth
def get_user_interviews():
    try:
        user_id = get_user_id()
        limit = request.args.get("limit", 50, type=int)
        interviews = supabase_client.get_user_interviews(user_id, limit)
        return jsonify(interviews)
    except Exception as e:
        logger.error(f"[API] get_user_interviews failed: {e}", exc_info=True)
        return jsonify([])


@app.route("/api/interview/save", methods=["POST"])
def save_interview_endpoint():
    try:
        data = request.json or {}
        user = get_current_user()
        if not user:
            return jsonify({"success": False, "message": "Not authenticated", "saved_to": "localStorage"}), 401
        user_id = user.user.id
        interview_id = supabase_client.save_interview(user_id, data)
        if interview_id:
            return jsonify({"success": True, "interview_id": interview_id, "saved_to": "database"})
        return jsonify({"success": False, "message": "Database save failed", "saved_to": "localStorage"}), 500
    except Exception as e:
        logger.error(f"[API] save_interview_endpoint failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e), "saved_to": "localStorage"}), 500


@app.route("/api/feedback/save", methods=["POST"])
def save_feedback_endpoint():
    try:
        data = request.json or {}
        user = get_current_user()
        if not user:
            return jsonify({"success": False, "message": "Not authenticated", "saved_to": "localStorage"}), 401
        user_id = user.user.id
        interview_id = data.get("interview_id")
        feedback_data = data.get("feedback")
        if not interview_id:
            return jsonify({"error": "interview_id required"}), 400
        ok = supabase_client.save_feedback(user_id, interview_id, feedback_data)
        if ok:
            return jsonify({"success": True, "saved_to": "database"})
        return jsonify({"success": False, "message": "DB save failed", "saved_to": "localStorage"}), 500
    except Exception as e:
        logger.error(f"[API] save_feedback failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e), "saved_to": "localStorage"}), 500


@app.route("/api/feedback/get/<interview_id>")
@require_auth
def get_feedback_by_id(interview_id):
    try:
        user_id = get_user_id()
        try:
            uuid.UUID(interview_id)
        except ValueError:
            return jsonify({"error": "Invalid interview ID"}), 400
        feedback = supabase_client.get_feedback(interview_id)
        if not feedback:
            return jsonify({}), 404
        if feedback.get("user_id") != user_id:
            return jsonify({"error": "Unauthorized"}), 403
        return jsonify(feedback)
    except Exception as e:
        logger.error(f"[API] get_feedback failed: {e}", exc_info=True)
        return jsonify({}), 500


def _format_conversation_with_merge(conv_dict):
    try:
        agent_msgs = conv_dict.get("agent", []) if isinstance(conv_dict, dict) else []
        user_msgs  = conv_dict.get("user", [])  if isinstance(conv_dict, dict) else []
        if not agent_msgs and not user_msgs:
            return []
        return merge_by_agent_turns(agent_msgs, user_msgs)
    except Exception as e:
        logger.error(f"[FORMAT] Error merging conversation: {e}", exc_info=True)
        return []


@app.route("/api/interview/<interview_id>")
@require_auth
def get_interview(interview_id):
    try:
        user_id = get_user_id()
        try:
            uuid.UUID(interview_id)
        except ValueError:
            return jsonify({"error": "Invalid interview ID format"}), 400

        interview = supabase_client.get_interview_by_id(user_id, interview_id)
        if not interview:
            interview = supabase_client.get_interview_by_room_name(user_id, interview_id)
        if not interview:
            return jsonify({"error": "Interview not found", "message": f"No interview: {interview_id}"}), 404

        conv = interview.get("conversation") or {}
        agent_msgs = conv.get("agent", [])
        user_msgs  = conv.get("user", [])
        ordered = merge_by_agent_turns(agent_msgs, user_msgs)
        merged_user_count = len([t for t in ordered if t.get("role") == "candidate"])
        stages_covered = list({m.get("stage") for m in agent_msgs if m.get("stage")})

        return jsonify({
            "ordered_conversation": ordered,
            "meta": {
                "candidate":             interview.get("candidate_name", "Unknown"),
                "interview_date":        interview.get("interview_date"),
                "room_name":             interview.get("room_name", ""),
                "job_role":              interview.get("job_role", ""),
                "experience_level":      interview.get("experience_level", ""),
                "final_stage":           interview.get("final_stage", ""),
                "ended_by":              interview.get("ended_by", "unknown"),
                "track":                 interview.get("track", "intro"),
                "total_agent_messages":  len(agent_msgs),
                "total_user_messages":   len(user_msgs),
                "merged_user_turns":     merged_user_count,
                "total_turns":           len(ordered),
                "stages_covered":        stages_covered,
                "stage_notes_count":     len(interview.get("stage_notes") or {}),
                "source":                "database",
            },
        })
    except Exception as e:
        logger.error(f"[API] get_interview failed: {e}", exc_info=True)
        return jsonify({"error": "Failed to load interview", "message": str(e)}), 500


@app.route("/api/interview/<filename>/summary")
def get_interview_summary_api(filename):
    try:
        if ".." in filename or "/" in filename or "\\" in filename:
            return jsonify({"error": "Invalid filename"}), 400
        summary = get_interview_summary(filename)
        if "error" in summary:
            return jsonify(summary), 404
        return jsonify({"success": True, **summary})
    except Exception as e:
        logger.error(f"[API] get_interview_summary failed: {e}", exc_info=True)
        return jsonify({"error": "Failed to get summary", "message": str(e)}), 500


# ===========================================================================
# FEEDBACK API (candidate-facing)
# ===========================================================================

_feedback_cache: dict = {}


@app.route("/api/feedback/cached/<interview_id>")
def get_cached_feedback(interview_id):
    if interview_id in _feedback_cache:
        cached = _feedback_cache[interview_id]
        return jsonify({
            "success": True,
            "interview_id": interview_id,
            "feedback": cached.get("feedback"),
            "cached_at": cached.get("cached_at"),
            "from_cache": True,
        })
    return jsonify({"error": "No cached feedback"}), 404


def _load_interview_context(interview_id):
    """Helper to load transcript + context for feedback generation."""
    try:
        user_id = get_user_id()
        if not user_id:
            return None, None, None, None, None, None, "Authentication required"
        try:
            uuid.UUID(interview_id)
        except ValueError:
            return None, None, None, None, None, None, f"Invalid interview ID format: {interview_id}"

        interview = supabase_client.get_interview_by_id(user_id, interview_id)
        if not interview:
            return None, None, None, None, None, None, f"Could not find interview: {interview_id}"

        raw_conv = interview.get("conversation") or {}
        conversation = _format_conversation_with_merge(raw_conv)
        if not conversation:
            return None, None, None, None, None, None, "No conversation found"

        lines = []
        for turn in conversation:
            role_label = "INTERVIEWER" if turn["role"] == "agent" else "CANDIDATE"
            stage_info = f" [{turn['stage']}]" if turn.get("stage") else ""
            lines.append(f"{role_label}{stage_info}: {turn['text']}")
        interview_chat = "\n\n".join(lines)

        meta = {
            "candidate":        interview.get("candidate_name", "Unknown"),
            "interview_date":   interview.get("interview_date"),
            "job_role":         interview.get("job_role"),
            "experience_level": interview.get("experience_level"),
            "track":            interview.get("track", "intro"),
            "source":           "database",
        }
        candidate_profile = f"Name: {meta['candidate']}"
        if meta.get("experience_level"):
            candidate_profile += f"\nExperience Level: {meta['experience_level']}"
        job_summary = f"Role: {meta.get('job_role', 'Not specified')}"

        return interview_chat, candidate_profile, job_summary, meta, conversation, raw_conv, None

    except Exception as e:
        logger.error(f"[FEEDBACK] context load error: {e}", exc_info=True)
        return None, None, None, None, None, None, f"Error loading interview: {e}"


@app.route("/api/feedback/scores", methods=["POST"])
@require_auth
def generate_feedback_scores():
    import json as _json
    from openai import OpenAI
    from prompts import FEEDBACKSCORES

    try:
        data = request.json or {}
        interview_id = data.get("interview_id")
        if not interview_id:
            return jsonify({"error": "Missing interview_id"}), 400

        chat, profile, job_summary, meta, conversation, raw_conv, err = _load_interview_context(interview_id)
        if err:
            return jsonify({"error": "Interview not found", "message": err}), 404

        from speech_analytics import analyze_transcript
        try:
            speech_data = analyze_transcript(raw_conv if isinstance(raw_conv, dict) else {})
        except Exception as e:
            logger.warning(f"[API] Speech analytics failed: {e}")
            speech_data = {}

        user_prompt = FEEDBACKSCORES.user_template.format(
            candidate_profile=profile, job_summary=job_summary, interview_chat=chat
        )
        user_id = get_user_id()
        keys = supabase_client.get_api_keys(user_id)
        if not keys or not keys.get("openai_key"):
            return jsonify({"error": "API key not configured", "message": "Configure OpenAI key in Settings"}), 400

        client = OpenAI(api_key=keys["openai_key"], timeout=10)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": FEEDBACKSCORES.system},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            parts = raw.split("\n")
            raw = "\n".join(parts[1:-1] if parts[-1].strip() == "```" else parts[1:])
        try:
            scores = _json.loads(raw)
        except _json.JSONDecodeError as e:
            logger.error(f"[API] Failed to parse scores JSON: {e}")
            scores = {
                "overall_score": 3.0,
                "summary_headline": "Analysis complete",
                "competencies": [
                    {"name": "Technical Skills",   "score": 3, "max_score": 5, "quick_take": "Demonstrated relevant experience"},
                    {"name": "Communication",      "score": 3, "max_score": 5, "quick_take": "Room for clearer responses"},
                    {"name": "Problem-Solving",    "score": 3, "max_score": 5, "quick_take": "Showed analytical thinking"},
                ],
                "top_strength": "Relevant project experience",
                "top_improvement": "Structure answers more clearly",
                "filler_word_count": 0,
                "answer_structure_score": 3,
            }

        return jsonify({
            "success": True,
            "interview_id": interview_id,
            "scores": scores,
            "speech_analytics": speech_data,
            "meta": {
                "candidate":      meta.get("candidate"),
                "interview_date": meta.get("interview_date"),
                "total_turns":    len(conversation),
                "model":          "gpt-4o-mini",
            },
        })
    except Exception as e:
        logger.error(f"[API] scores extraction error: {e}", exc_info=True)
        return jsonify({"error": "Scores extraction failed", "message": str(e)}), 500


@app.route("/api/feedback", methods=["POST"])
@require_auth
def generate_feedback():
    import json as _json
    from openai import OpenAI
    from prompts import build_post_interview_feedback_prompt

    try:
        data = request.json or {}
        interview_id = data.get("interview_id")
        provided_scores = data.get("scores")
        if not interview_id:
            return jsonify({"error": "Missing interview_id"}), 400

        chat, profile, job_summary, meta, conversation, raw_conv, err = _load_interview_context(interview_id)
        if err:
            return jsonify({"error": "Interview not found", "message": err}), 404

        from speech_analytics import analyze_transcript
        try:
            speech_data = analyze_transcript(raw_conv if isinstance(raw_conv, dict) else {})
        except Exception as e:
            logger.warning(f"[API] speech analytics failed: {e}")
            speech_data = {}

        speech_json = _json.dumps(speech_data, indent=2)
        system_prompt = build_post_interview_feedback_prompt()
        user_prompt = f"""Please analyze this mock interview and provide detailed feedback.

<CANDIDATE_PROFILE>
{profile}
</CANDIDATE_PROFILE>

<JOB_SUMMARY>
{job_summary}
</JOB_SUMMARY>

<INTERVIEW_TRACK>
{meta.get("track", "intro")}
</INTERVIEW_TRACK>

<SPEECH_ANALYTICS>
{speech_json}
</SPEECH_ANALYTICS>

<INTERVIEW_CHAT>
{chat}
</INTERVIEW_CHAT>

Provide your analysis and feedback following the output format specified.
Include a brief Speech Analytics note mentioning filler word count ({speech_data.get('filler_total', 'N/A')}) and average pace ({speech_data.get('avg_words_per_minute', 'N/A')} WPM)."""

        user_id = get_user_id()
        keys = supabase_client.get_api_keys(user_id)
        if not keys or not keys.get("openai_key"):
            return jsonify({"error": "API key not configured", "message": "Configure OpenAI key in Settings"}), 400

        client = OpenAI(api_key=keys["openai_key"], timeout=30)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=3000,
        )
        feedback_text = resp.choices[0].message.content
        _feedback_cache[interview_id] = {"feedback": feedback_text, "cached_at": time.time(), "model": "gpt-4o-mini"}

        response_data = {
            "success": True,
            "interview_id": interview_id,
            "feedback": feedback_text,
            "speech_analytics": speech_data,
            "meta": {
                "candidate":      meta.get("candidate"),
                "interview_date": meta.get("interview_date"),
                "total_turns":    len(conversation),
                "model":          "gpt-4o-mini",
            },
        }
        if provided_scores:
            response_data["scores"] = provided_scores
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"[API] Feedback error: {e}", exc_info=True)
        return jsonify({"error": "Feedback generation failed", "message": str(e)}), 500


# ===========================================================================
# EVAL REPORT API (Phase 3 — stubbed in Phase 1)
# ===========================================================================

@app.route("/api/eval-report/<interview_id>")
@require_auth
def get_eval_report_api(interview_id):
    """Phase 3 returns the SessionEvalReport. Phase 1 stub returns 501."""
    user_id = get_user_id()
    try:
        uuid.UUID(interview_id)
    except ValueError:
        return jsonify({"error": "Invalid interview ID format"}), 400

    report = supabase_client.get_eval_report(user_id, interview_id)
    if report:
        return jsonify(report)
    return jsonify({
        "error": "Eval pipeline not yet implemented",
        "message": "Eval reports are produced in Phase 3.",
        "phase": 1,
    }), 501


# ===========================================================================
# SKIP STAGE API (validated against StageRegistry)
# ===========================================================================

@app.route("/api/skip-stage", methods=["POST"])
def skip_stage():
    try:
        data = request.json or {}
        room_name    = data.get("room_name")
        target_stage = data.get("target_stage")
        track        = data.get("track", "intro")

        if not room_name:
            return jsonify({"error": "Missing room_name"}), 400
        if not target_stage:
            return jsonify({"error": "Missing target_stage"}), 400

        try:
            track_enum = TrackType(track)
        except ValueError:
            return jsonify({"error": f"Invalid track '{track}'"}), 400

        valid_stages = StageRegistry.get_stage_ids(track_enum)
        if target_stage not in valid_stages:
            return jsonify({
                "error": "Invalid target_stage",
                "message": f"Valid stages for {track}: {valid_stages}",
            }), 400

        logger.info(f"[API] Skip stage request: {room_name} -> {target_stage}")
        return jsonify({
            "success": True,
            "room_name": room_name,
            "target_stage": target_stage,
            "message": f"Skip queued. The agent will transition to {target_stage} shortly.",
        })
    except Exception as e:
        logger.error(f"[API] skip_stage error: {e}", exc_info=True)
        return jsonify({"error": "Skip request failed", "message": str(e)}), 500


# ===========================================================================
# HEALTH
# ===========================================================================

@app.route("/health")
def health_check():
    try:
        if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_KEY"):
            raise ValueError("Supabase credentials not configured")
        return jsonify({
            "status": "healthy",
            "database": "configured",
            "workers": {
                "active": len(worker_manager.active_workers),
                "max":    worker_manager.max_workers,
            },
            "tracks_implemented": [t.value for t in StageRegistry.implemented_tracks()],
        }), 200
    except Exception as e:
        logger.error(f"[HEALTH] check failed: {e}", exc_info=True)
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


# ===========================================================================
# ERROR HANDLERS
# ===========================================================================

@app.errorhandler(404)
def not_found(e):
    logger.warning(f"[ERROR] 404 - {request.path}")
    return render_template("error.html", error="Page not found"), 404


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"[ERROR] 500 - {str(e)}", exc_info=True)
    return render_template("error.html", error="Internal server error"), 500


if __name__ == "__main__":
    logger.info("[MAIN] Starting VoiceLoop Flask server")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    logger.info(f"[MAIN] http://localhost:{port}")
    app.run(
        debug=debug,
        port=port,
        host="0.0.0.0",
        use_reloader=False,  # don't kill spawned workers on autoreload
    )

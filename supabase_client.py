"""
VoiceLoop — Supabase persistence layer

Adapted from MockFlow with two changes for Phase 1:
  1. save_interview() now writes `stage_notes` (jsonb dict[stage_id → AgentNote])
     and drops coding-specific fields.
  2. New eval-pipeline methods are stubbed (raise NotImplementedError) so the
     contract is in place — agent_worker references them via a try/except guard.

Phase 3 implements the eval methods for real.

Encryption: FERNET_KEY is the canonical env var (per INIT.md), with ENCRYPTION_KEY
kept as a legacy alias for users migrating from MockFlow .env files.
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Service-role client. Bypasses RLS. Server-side only."""

    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in environment")

        self.client: Client = create_client(url, key)

        # Accept FERNET_KEY (canonical) or ENCRYPTION_KEY (legacy alias).
        raw_key = os.getenv("FERNET_KEY") or os.getenv("ENCRYPTION_KEY") or ""
        self.encryption_key = raw_key.encode()
        self.cipher: Optional[Fernet] = Fernet(self.encryption_key) if self.encryption_key else None

    # ------------------------------------------------------------------
    # Crypto
    # ------------------------------------------------------------------

    def _encrypt(self, text: str) -> str:
        if not self.cipher:
            raise ValueError("FERNET_KEY (or ENCRYPTION_KEY) is not configured")
        return self.cipher.encrypt(text.encode()).decode()

    def _decrypt(self, encrypted_text: str) -> str:
        if not self.cipher:
            raise ValueError("FERNET_KEY (or ENCRYPTION_KEY) is not configured")
        return self.cipher.decrypt(encrypted_text.encode()).decode()

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.client.table("users").select("*").eq("id", user_id).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error(f"[SUPABASE] get_user failed: {e}")
            return None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.client.table("users").select("*").eq("email", email).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error(f"[SUPABASE] get_user_by_email failed: {e}")
            return None

    # ------------------------------------------------------------------
    # BYOK API keys
    # ------------------------------------------------------------------

    def save_api_keys(
        self,
        user_id: str,
        livekit_url: str,
        livekit_api_key: str,
        livekit_api_secret: str,
        openai_key: str,
        deepgram_key: str,
    ) -> bool:
        try:
            data = {
                "user_id": user_id,
                "livekit_url_encrypted":    self._encrypt(livekit_url),
                "livekit_key_encrypted":    self._encrypt(livekit_api_key),
                "livekit_secret_encrypted": self._encrypt(livekit_api_secret),
                "openai_key_encrypted":     self._encrypt(openai_key),
                "deepgram_key_encrypted":   self._encrypt(deepgram_key),
                "encryption_salt":          "salt_v1",
            }
            existing = self.client.table("user_api_keys").select("id").eq("user_id", user_id).execute()
            if existing.data:
                self.client.table("user_api_keys").update(data).eq("user_id", user_id).execute()
            else:
                self.client.table("user_api_keys").insert(data).execute()
            return True
        except Exception as e:
            logger.error(f"[SUPABASE] save_api_keys failed for user {user_id}: {e}", exc_info=True)
            return False

    def get_api_keys(self, user_id: str) -> Optional[Dict[str, str]]:
        try:
            r = self.client.table("user_api_keys").select("*").eq("user_id", user_id).execute()
            if not r.data:
                return None
            row = r.data[0]
            return {
                "livekit_url":        self._decrypt(row["livekit_url_encrypted"]),
                "livekit_api_key":    self._decrypt(row["livekit_key_encrypted"]),
                "livekit_api_secret": self._decrypt(row["livekit_secret_encrypted"]),
                "openai_key":         self._decrypt(row["openai_key_encrypted"]),
                "deepgram_key":       self._decrypt(row["deepgram_key_encrypted"]),
            }
        except Exception as e:
            logger.error(f"[SUPABASE] get_api_keys failed for user {user_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Interviews
    # ------------------------------------------------------------------

    def save_interview(self, user_id: str, interview_data: Dict[str, Any]) -> Optional[str]:
        """
        Save an interview row. Returns the new interview's UUID.

        Accepts both snake_case (from agent_worker.py) and camelCase (from
        the frontend) for compatibility. The agent_worker is the primary
        caller and always uses snake_case.

        New for VoiceLoop: `stage_notes` (dict[stage_id → AgentNote dict])
        is persisted to the `stage_notes jsonb` column.
        """
        try:
            def pick(*keys, default=None):
                for k in keys:
                    if k in interview_data and interview_data[k] is not None:
                        return interview_data[k]
                return default

            candidate_name   = pick("candidate_name", "candidate", "candidateName", default="Unknown")
            room_name        = pick("room_name", "roomName", default="")
            job_role         = pick("job_role", "jobRole", default="")
            experience_level = pick("experience_level", "experienceLevel", default="")
            company_name     = pick("company_name", "companyName", default=None)
            track            = pick("track", default="intro")
            final_stage      = pick("final_stage", "finalStage", default="")
            ended_by         = pick("ended_by", "endedBy", default="unknown")
            skipped_stages   = pick("skipped_stages", "skippedStages", default=[])
            has_resume       = pick("has_resume", "hasResume", default=False)
            has_jd           = pick("has_jd", "hasJobDescription", default=False)

            data = {
                "user_id":          user_id,
                "candidate_name":   candidate_name,
                "room_name":        room_name,
                "job_role":         job_role,
                "experience_level": experience_level,
                "company_name":     company_name,
                "final_stage":      final_stage,
                "ended_by":         ended_by,
                "skipped_stages":   skipped_stages,
                "has_resume":       has_resume,
                "has_jd":           has_jd,
                "conversation":     interview_data.get("conversation", {}),
                "total_messages":   interview_data.get("total_messages", {}),
                "stage_notes":      interview_data.get("stage_notes", {}),
                "metadata":         interview_data.get("metadata", {}),
                "interview_date":   interview_data.get("interview_date"),
                "track":            track,
                "track_config":     interview_data.get("track_config", {}),
            }

            logger.info(f"[SUPABASE] Saving interview for candidate: {candidate_name} (track={track})")
            r = self.client.table("interviews").insert(data).execute()
            if r.data:
                interview_id = r.data[0]["id"]
                logger.info(f"[SUPABASE] Interview saved: {interview_id}")
                return interview_id
            return None
        except Exception as e:
            logger.error(f"[SUPABASE] save_interview failed: {e}", exc_info=True)
            return None

    def get_user_interviews(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            r = (
                self.client.table("interviews").select("*")
                .eq("user_id", user_id)
                .order("interview_date", desc=True)
                .limit(limit)
                .execute()
            )
            return r.data or []
        except Exception as e:
            logger.error(f"[SUPABASE] get_user_interviews failed: {e}")
            return []

    def get_interview_by_room(self, room_name: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.client.table("interviews").select("*").eq("room_name", room_name).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error(f"[SUPABASE] get_interview_by_room failed: {e}")
            return None

    def get_interview_by_room_name(self, user_id: str, room_name: str) -> Optional[Dict[str, Any]]:
        try:
            r = (
                self.client.table("interviews").select("*")
                .eq("user_id", user_id).eq("room_name", room_name)
                .execute()
            )
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error(f"[SUPABASE] get_interview_by_room_name failed: {e}")
            return None

    def get_interview_by_id(self, user_id: str, interview_id: str) -> Optional[Dict[str, Any]]:
        try:
            r = (
                self.client.table("interviews").select("*")
                .eq("id", interview_id).eq("user_id", user_id)
                .execute()
            )
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error(f"[SUPABASE] get_interview_by_id failed: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Candidate feedback (unchanged from MockFlow)
    # ------------------------------------------------------------------

    def save_feedback(self, user_id: str, interview_id: str, feedback_data: Dict[str, Any]) -> bool:
        try:
            self.client.table("feedback").insert({
                "user_id": user_id,
                "interview_id": interview_id,
                "feedback_data": feedback_data,
            }).execute()
            return True
        except Exception as e:
            logger.error(f"[SUPABASE] save_feedback failed: {e}")
            return False

    def get_feedback(self, interview_id: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.client.table("feedback").select("*").eq("interview_id", interview_id).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error(f"[SUPABASE] get_feedback failed: {e}")
            return None

    # ==================================================================
    # Eval-layer methods — Phase 3+
    #
    # Stubbed in Phase 1 so agent_worker.py can reference them behind a
    # try/except. Phase 3 replaces each `raise NotImplementedError` with
    # the real insert/select against `eval_reports` and `stage_eval_reports`.
    # ==================================================================

    def save_eval_report(self, user_id: str, interview_id: str, session_eval_dict: dict,
                          overall_agent_pass: Optional[bool] = None,
                          overall_candidate_signal: Optional[str] = None,
                          rubric_version: int = 1) -> Optional[str]:
        """Phase 3: insert one row into eval_reports. Returns the new id."""
        logger.warning(
            f"[SUPABASE] save_eval_report stubbed in Phase 1 (interview_id={interview_id})"
        )
        return None

    def save_stage_eval_reports(self, eval_report_id: str, interview_id: str,
                                 stage_reports: List[dict]) -> bool:
        """Phase 3: bulk-insert per-stage rows into stage_eval_reports."""
        logger.warning(
            f"[SUPABASE] save_stage_eval_reports stubbed in Phase 1 (eval_report_id={eval_report_id})"
        )
        return False

    def get_eval_report(self, user_id: str, interview_id: str) -> Optional[Dict[str, Any]]:
        """Phase 3: fetch the eval_reports row for an interview."""
        logger.warning("[SUPABASE] get_eval_report stubbed in Phase 1")
        return None

    def get_stage_eval_reports(self, eval_report_id: str) -> List[Dict[str, Any]]:
        """Phase 3: fetch per-stage rows joined to an eval_report."""
        logger.warning("[SUPABASE] get_stage_eval_reports stubbed in Phase 1")
        return []

    def get_agent_performance_trend(self, user_id: str, n_sessions: int = 10) -> Dict[str, Any]:
        """Phase 4 (MCP-exposed): aggregate agent scores across recent sessions."""
        logger.warning("[SUPABASE] get_agent_performance_trend stubbed in Phase 1")
        return {}

    def compare_sessions(self, session_ids: List[str]) -> Dict[str, Any]:
        """Phase 4 (MCP-exposed): cross-session diff."""
        logger.warning("[SUPABASE] compare_sessions stubbed in Phase 1")
        return {}


# Global singleton consumed by app.py and agent_worker.py.
supabase_client = SupabaseClient()

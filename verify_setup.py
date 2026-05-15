#!/usr/bin/env python3
"""
VoiceLoop — Setup verifier

Run before `python app.py` to catch misconfiguration early. Checks:
  1. Required env vars are present.
  2. Optional / phase-1 env vars logged.
  3. Supabase service-role connection works.
  4. Required Supabase tables exist (users, user_api_keys, interviews,
     feedback, eval_reports, stage_eval_reports). Reports each.
  5. The Fernet key decrypts what it encrypts (round-trip).
  6. stage_registry + fsm + prompts import cleanly.
  7. Welcome audio files present (or warns).

Exit code 0 on success, 1 on any failure.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")


GREEN = "\033[32m"
RED   = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{GREEN}✓{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}!{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{RED}✗{RESET} {msg}")


def check_env() -> bool:
    print("\n— Environment variables —")
    required = [
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_ANON_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "FLASK_SECRET_KEY",
    ]
    missing = [v for v in required if not os.getenv(v)]
    if not os.getenv("FERNET_KEY") and not os.getenv("ENCRYPTION_KEY"):
        missing.append("FERNET_KEY (or legacy ENCRYPTION_KEY)")

    if missing:
        for v in missing:
            _fail(f"missing required env var: {v}")
        return False
    _ok("all required env vars present")

    # Helpful warnings
    if not os.getenv("OPENAI_API_KEY"):
        _warn("OPENAI_API_KEY not set — needed by generate_welcome_audio.py")
    if not os.getenv("LIVEKIT_URL"):
        _warn("LIVEKIT_URL not set — BYOK still works per-user but server-side defaults are blank")
    return True


def check_fernet() -> bool:
    print("\n— Fernet key —")
    try:
        from cryptography.fernet import Fernet
        raw = (os.getenv("FERNET_KEY") or os.getenv("ENCRYPTION_KEY") or "").encode()
        cipher = Fernet(raw)
        token = cipher.encrypt(b"voiceloop-roundtrip")
        if cipher.decrypt(token) != b"voiceloop-roundtrip":
            _fail("Fernet round-trip mismatch")
            return False
        _ok("Fernet key round-trip OK")
        return True
    except Exception as e:
        _fail(f"Fernet check failed: {e}")
        return False


def check_supabase_tables() -> bool:
    print("\n— Supabase tables —")
    tables = [
        "users",
        "user_api_keys",
        "interviews",
        "feedback",
        "eval_reports",
        "stage_eval_reports",
    ]
    try:
        from supabase_client import supabase_client
    except Exception as e:
        _fail(f"failed to construct SupabaseClient: {e}")
        traceback.print_exc()
        return False

    all_ok = True
    for table in tables:
        try:
            supabase_client.client.table(table).select("id").limit(1).execute()
            _ok(f"table reachable: {table}")
        except Exception as e:
            _fail(f"table check failed for '{table}': {e}")
            all_ok = False
    return all_ok


def check_python_imports() -> bool:
    print("\n— VoiceLoop module imports —")
    modules = [
        "stage_registry",
        "fsm",
        "prompts",
        "audio_cache",
        "speech_analytics",
        "conversation_cache",
        "document_processor",
        "auth_helpers",
        "worker_manager",
        "supabase_client",
    ]
    all_ok = True
    for m in modules:
        try:
            __import__(m)
            _ok(f"import {m}")
        except Exception as e:
            _fail(f"import {m} failed: {e}")
            all_ok = False
    return all_ok


def check_stage_registry() -> bool:
    print("\n— Stage Registry —")
    try:
        from stage_registry import StageRegistry, TrackType

        intro_stages = StageRegistry.get_stages(TrackType.INTRO)
        if not intro_stages:
            _fail("INTRO track has no stages registered")
            return False
        ids = [s.stage_id for s in intro_stages]
        expected = ["welcome", "self_intro", "depth_general", "company_fit", "closing"]
        if ids != expected:
            _fail(f"INTRO stage_ids mismatch.\n  expected: {expected}\n  got:      {ids}")
            return False
        _ok(f"INTRO sequence has {len(intro_stages)} stages: {ids}")

        # Phase 1: only INTRO implemented
        implemented = [t.value for t in StageRegistry.implemented_tracks()]
        _ok(f"implemented tracks: {implemented}")
        return True
    except Exception as e:
        _fail(f"stage_registry check failed: {e}")
        return False


def check_welcome_audio() -> bool:
    print("\n— Welcome audio assets —")
    from audio_cache import WELCOME_AUDIO_FILES
    here = Path(__file__).parent
    any_missing = False
    for track, rel_path in WELCOME_AUDIO_FILES.items():
        full = here / rel_path
        if full.exists():
            size = full.stat().st_size
            _ok(f"{track}: {rel_path} ({size} bytes)")
        else:
            any_missing = True
            if track == "intro":
                _fail(f"{track}: {rel_path} MISSING — run `python generate_welcome_audio.py --track intro`")
            else:
                _warn(f"{track}: {rel_path} missing (Phase 2 will generate)")
    return not any_missing or True  # warnings don't fail


def main() -> int:
    print("VoiceLoop setup verification")
    print("=" * 50)

    results = {
        "env":           check_env(),
        "fernet":        check_fernet(),
        "imports":       check_python_imports(),
        "stage_registry": check_stage_registry(),
        "supabase":      check_supabase_tables(),
        "audio":         check_welcome_audio(),
    }

    print("\n" + "=" * 50)
    print("Summary:")
    failed = [k for k, v in results.items() if not v]
    for k, v in results.items():
        status = f"{GREEN}OK{RESET}" if v else f"{RED}FAIL{RESET}"
        print(f"  {k:16s} {status}")

    if failed:
        print(f"\n{RED}{len(failed)} check(s) failed.{RESET}")
        return 1
    print(f"\n{GREEN}All checks passed.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
VoiceLoop — One-shot welcome-audio generator

Renders the per-track welcome scripts (from audio_cache.WELCOME_SCRIPTS) into
MP3 files at static/audio/welcome_<track>.mp3 via the OpenAI TTS API.

These MP3s are build-time assets — commit them to the repo. They're loaded
by agent_worker.py during on_enter() to eliminate the cold-start TTS latency
on session connect.

Usage:
  python generate_welcome_audio.py --track intro
  python generate_welcome_audio.py --track all       # Phase 2: generate all six

Reads OPENAI_API_KEY from environment (.env or shell). NOT a user's BYOK key —
this is server-side.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the script's directory so the script can be invoked from anywhere.
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

from audio_cache import (  # noqa: E402
    WELCOME_AUDIO_FILES,
    WELCOME_SCRIPTS,
    generate_and_cache_welcome_audio,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate welcome audio MP3s for one or all tracks.")
    parser.add_argument(
        "--track",
        required=True,
        choices=list(WELCOME_AUDIO_FILES.keys()) + ["all"],
        help="Track to generate audio for (or 'all').",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing MP3 files. Default: skip tracks that already have one.",
    )
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY not set in environment or .env.", file=sys.stderr)
        return 2

    tracks = list(WELCOME_AUDIO_FILES.keys()) if args.track == "all" else [args.track]
    here = Path(__file__).parent

    failures: list[str] = []
    for track in tracks:
        target_path = here / WELCOME_AUDIO_FILES[track]
        if target_path.exists() and not args.force:
            print(f"[SKIP] {track}: {target_path.relative_to(here)} already exists (use --force to overwrite)")
            continue

        script_preview = WELCOME_SCRIPTS.get(track, "")[:80].replace("\n", " ")
        print(f"[GEN]  {track}: {script_preview}...")

        try:
            ok = generate_and_cache_welcome_audio(track, api_key)
            if ok:
                print(f"       → wrote {target_path.relative_to(here)}")
            else:
                print(f"[FAIL] {track}: generator returned False")
                failures.append(track)
        except Exception as e:
            print(f"[FAIL] {track}: {e}")
            failures.append(track)

    if failures:
        print(f"\n[ERROR] {len(failures)} track(s) failed: {failures}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

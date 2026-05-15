"""
Audio Cache Module

Handles pre-generated welcome audio playback for each interview track.
Audio files are pre-committed to static/audio/ and played at session start
instead of generating TTS each time.

If a file is missing, falls back to in-session TTS generation (logs WARNING).
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Map VoiceLoop track type (= TrackType.value) → pre-generated audio file path.
# Phase 1 only ships the 'intro' MP3. Phase 2 generates the rest.
WELCOME_AUDIO_FILES = {
    'intro':         'static/audio/welcome_intro.mp3',
    'behavioral':    'static/audio/welcome_behavioral.mp3',
    'technical_swe': 'static/audio/welcome_technical_swe.mp3',
    'ds_ml':         'static/audio/welcome_ds_ml.mp3',
    'analytics':     'static/audio/welcome_analytics.mp3',
    'product':       'static/audio/welcome_product.mp3',
}

# Welcome scripts used both for fallback TTS generation and for the
# generate_welcome_audio.py CLI that pre-renders the MP3s.
WELCOME_SCRIPTS = {
    'intro': (
        "Welcome to your mock interview. I'm Alex, your AI interviewer. "
        "We'll go through a few stages: you'll introduce yourself, discuss your past experience, "
        "explore how you fit the role, and wrap up. Let's get started!"
    ),
    'behavioral': (
        "Welcome to your behavioral mock interview. I'm Alex, your AI interviewer. "
        "In this session, I'll ask you behavioral questions about your past experiences. "
        "Think about specific situations, your actions, and the results you achieved. "
        "Ready when you are!"
    ),
    'technical_swe': (
        "Welcome to your technical software engineering interview. I'm Alex, your AI interviewer. "
        "We'll explore your technical knowledge through conceptual questions, then discuss a system design problem. "
        "No live coding today - just talk me through your thinking. Let's begin!"
    ),
    'ds_ml': (
        "Welcome to your data science and machine learning interview. I'm Alex, your AI interviewer. "
        "We'll discuss conceptual topics across modeling, evaluation, and pipelines, "
        "and then design an end-to-end ML system together. Ready when you are!"
    ),
    'analytics': (
        "Welcome to your analytics interview. I'm Alex, your AI interviewer. "
        "We'll talk through your experience, work through a SQL problem, and discuss a business case. "
        "Let's get started!"
    ),
    'product': (
        "Welcome to your product strategy interview. I'm Alex, your AI interviewer. "
        "We'll explore product thinking, metrics, and a strategic case study. "
        "Ready when you are!"
    ),
}


def get_welcome_audio_bytes(track_type: str) -> Optional[bytes]:
    """
    Return the bytes of the pre-generated welcome audio for the given track.
    Returns None if the file does not exist (caller should fall back to TTS).

    Args:
        track_type: 'intro', 'behavioral', 'technical_voice', or 'coding'

    Returns:
        Audio bytes, or None if not found
    """
    file_path = WELCOME_AUDIO_FILES.get(track_type)
    if not file_path:
        logger.warning(f"[AUDIO] No welcome audio file configured for track: {track_type}")
        return None

    abs_path = os.path.join(os.path.dirname(__file__), file_path)
    if not os.path.exists(abs_path):
        logger.warning(f"[AUDIO] Welcome audio file missing: {abs_path}. Will use TTS fallback.")
        return None

    try:
        with open(abs_path, 'rb') as f:
            audio_bytes = f.read()
        logger.info(f"[AUDIO] Loaded welcome audio for track '{track_type}': {len(audio_bytes)} bytes")
        return audio_bytes
    except Exception as e:
        logger.error(f"[AUDIO] Failed to read welcome audio file {abs_path}: {e}")
        return None


def get_welcome_script(track_type: str) -> str:
    """
    Return the welcome speech text for a track.
    Used as fallback TTS text when audio file is missing.

    Args:
        track_type: Track type string

    Returns:
        Welcome script text
    """
    return WELCOME_SCRIPTS.get(track_type, WELCOME_SCRIPTS['intro'])


def generate_and_cache_welcome_audio(track_type: str, openai_api_key: str) -> bool:
    """
    Generate welcome audio via OpenAI TTS and save to static/audio/.
    Used as a one-time setup script or runtime fallback.

    Args:
        track_type: Track type string
        openai_api_key: OpenAI API key for TTS

    Returns:
        True if successful
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_api_key)

        file_path = WELCOME_AUDIO_FILES.get(track_type)
        if not file_path:
            logger.error(f"[AUDIO] Unknown track type: {track_type}")
            return False

        script = get_welcome_script(track_type)
        abs_path = os.path.join(os.path.dirname(__file__), file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        response = client.audio.speech.create(
            model='tts-1',
            voice='alloy',
            input=script,
        )

        with open(abs_path, 'wb') as f:
            f.write(response.content)

        logger.info(f"[AUDIO] Generated and cached welcome audio for track '{track_type}': {abs_path}")
        return True

    except Exception as e:
        logger.error(f"[AUDIO] Failed to generate welcome audio for track '{track_type}': {e}")
        return False

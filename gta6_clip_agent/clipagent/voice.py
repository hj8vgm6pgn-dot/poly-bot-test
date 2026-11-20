"""Text-to-speech for the commentary track.

The commentary is what makes a clip of someone else's footage *your* video in the
eyes of the platforms' reused-content rules, so every clip gets a short original
line written by Claude. Providers, in order of preference:

  elevenlabs  - natural voices, needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID
  piper       - free local neural voices, needs the `piper` binary and PIPER_MODEL
  espeak      - free and robotic; a last resort

If none is available the commentary is still burned in as on-screen captions.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import requests

from .config import settings

log = logging.getLogger(__name__)


def available_provider() -> str | None:
    want = settings.tts_provider
    if want == "none":
        return None
    checks = {
        "elevenlabs": bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id),
        "piper": bool(shutil.which("piper") and settings.piper_model),
        "espeak": bool(shutil.which("espeak-ng")),
    }
    if want != "auto":
        return want if checks.get(want) else None
    return next((p for p in ("elevenlabs", "piper", "espeak") if checks[p]), None)


def synthesize(text: str, out_path: Path) -> Path | None:
    """Write speech for ``text`` to ``out_path`` (extension set per provider). None if unavailable."""
    text = " ".join(text.split())
    provider = available_provider()
    if not text or not provider:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if provider == "elevenlabs":
            out = out_path.with_suffix(".mp3")
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
                headers={"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": settings.elevenlabs_model},
                timeout=120,
            )
            r.raise_for_status()
            out.write_bytes(r.content)
            return out
        out = out_path.with_suffix(".wav")
        if provider == "piper":
            subprocess.run(["piper", "--model", settings.piper_model, "--output_file", str(out)],
                           input=text, text=True, check=True, capture_output=True)
        else:
            subprocess.run(["espeak-ng", "-s", "165", "-w", str(out), text], check=True, capture_output=True)
        return out
    except (requests.RequestException, subprocess.CalledProcessError, OSError) as e:
        log.warning("voiceover failed (%s): %s - falling back to captions only", provider, e)
        return None

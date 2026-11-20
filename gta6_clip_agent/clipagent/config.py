"""Central configuration, loaded from environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


# (font file, family name for subtitles) - first one that exists is the default.
_FONTS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu Sans"),
    ("C:/Windows/Fonts/arialbd.ttf", "Arial"),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "Arial"),
    ("/Library/Fonts/Arial Bold.ttf", "Arial"),
]


def _default_font() -> tuple[str, str]:
    return next(((f, n) for f, n in _FONTS if Path(f).exists()), _FONTS[0])


@dataclass
class Settings:
    # Paths
    footage_dir: Path = field(default_factory=lambda: Path(_env("FOOTAGE_DIR", "footage")))
    output_dir: Path = field(default_factory=lambda: Path(_env("OUTPUT_DIR", "out")))
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "out/clipagent.db")))
    font_file: str = _env("FONT_FILE", _default_font()[0])
    subtitle_font: str = _env("SUBTITLE_FONT", _default_font()[1])  # family name of FONT_FILE

    # Clip shape
    min_clip_s: float = float(_env("MIN_CLIP_S", "12"))
    max_clip_s: float = float(_env("MAX_CLIP_S", "35"))
    candidates_per_video: int = int(_env("CANDIDATES_PER_VIDEO", "6"))

    # Claude
    claude_model: str = _env("CLAUDE_MODEL", "claude-opus-5-5")
    claude_effort: str = _env("CLAUDE_EFFORT", "high")

    # YouTube (Data API v3)
    youtube_api_key: str | None = _env("YOUTUBE_API_KEY")
    youtube_client_id: str | None = _env("YOUTUBE_CLIENT_ID")
    youtube_client_secret: str | None = _env("YOUTUBE_CLIENT_SECRET")
    youtube_refresh_token: str | None = _env("YOUTUBE_REFRESH_TOKEN")

    # Instagram (Graph API, Business/Creator account)
    ig_user_id: str | None = _env("IG_USER_ID")
    ig_access_token: str | None = _env("IG_ACCESS_TOKEN")
    # Reels publishing needs a public URL for the video file (e.g. an S3/R2 bucket).
    public_video_base_url: str | None = _env("PUBLIC_VIDEO_BASE_URL")

    # TikTok (Display API + Content Posting API)
    tiktok_access_token: str | None = _env("TIKTOK_ACCESS_TOKEN")

    # Trend search
    search_terms: list[str] = field(
        default_factory=lambda: (_env("SEARCH_TERMS", "GTA 6,GTA VI,GTA 6 gameplay") or "").split(",")
    )

    # Only videos from approved channels that mention one of these are picked up.
    source_keywords: list[str] = field(
        default_factory=lambda: (_env("SOURCE_KEYWORDS", "gta 6,gta vi,gta6,grand theft auto vi") or "").split(",")
    )

    # Speech-to-text (subtitles + picking moments by what is said)
    whisper_model: str = _env("WHISPER_MODEL", "small")
    # Campaign-style clips: short, original audio, subtitles
    campaign_min_s: float = float(_env("CAMPAIGN_MIN_S", "10"))
    campaign_max_s: float = float(_env("CAMPAIGN_MAX_S", "20"))

    # Voiceover (your channel's original commentary track)
    # Your own recordings: voice/clip_00012.m4a is used for clip #12 and pitched by VOICE_PITCH.
    voice_dir: Path = field(default_factory=lambda: Path(_env("VOICE_DIR", "voice")))
    voice_pitch: float = float(_env("VOICE_PITCH", "-3"))  # semitones; negative = deeper
    voice_preset: str | None = _env("VOICE_PRESET")  # a named effect; overrides VOICE_PITCH
    tts_provider: str = _env("TTS_PROVIDER", "auto")  # auto | elevenlabs | piper | espeak | none
    elevenlabs_api_key: str | None = _env("ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str | None = _env("ELEVENLABS_VOICE_ID")
    elevenlabs_model: str = _env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    piper_model: str | None = _env("PIPER_MODEL")

    def ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.footage_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()

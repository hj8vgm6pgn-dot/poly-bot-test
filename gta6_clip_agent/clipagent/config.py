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


@dataclass
class Settings:
    # Paths
    footage_dir: Path = field(default_factory=lambda: Path(_env("FOOTAGE_DIR", "footage")))
    output_dir: Path = field(default_factory=lambda: Path(_env("OUTPUT_DIR", "out")))
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "out/clipagent.db")))
    font_file: str = _env("FONT_FILE", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

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

    def ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.footage_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()

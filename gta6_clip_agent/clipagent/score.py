"""Ask Claude to judge each candidate moment and write the packaging (hook, title, caption).

Signal processing finds *loud, busy* moments; Claude decides whether they are
actually *interesting* (a funny glitch, a wild chase, a new-map discovery) and
how to package them, using what is trending right now and what has worked on
your own accounts before.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field, ValidationError

from .config import settings

SYSTEM_PROMPT = """You are a short-form video strategist for a GTA 6 gaming channel that posts \
to TikTok, Instagram Reels and YouTube Shorts.

You are shown frames from a candidate gameplay moment (in order, evenly spaced) plus context: \
what GTA 6 topics are trending right now and which of the channel's own past posts performed best.

Judge how likely this moment is to go viral as a vertical short. Weigh:
- Hook: is something visually striking or surprising in the first 1-3 seconds?
- Payoff: does it build to a clear moment (crash, escape, glitch, funny NPC, stunt, discovery)?
- Novelty: does it show something viewers have not seen a thousand times, or ride a current trend?
- Readability in 9:16: will the action still be clear after a center crop on a phone?

Be honest: most moments are average. Reserve scores above 80 for clips you would bet on.
Write packaging in the voice of gaming TikTok: punchy, curiosity-driven, no clickbait that the \
clip does not deliver on, no claims of being official Rockstar content, and no spoilers in the hook \
text unless the clip itself is the spoiler. Hook text is burned on screen, so keep it under 45 characters."""


class ClipJudgement(BaseModel):
    virality_score: int = Field(description="0-100 likelihood this performs well as a short")
    keep: bool = Field(description="Whether this clip is worth rendering at all")
    category: str = Field(description="e.g. chase, crash, glitch, funny NPC, stunt, scenery, combat")
    hook_text: str = Field(description="On-screen text for the first 3 seconds, <=45 chars")
    title: str = Field(description="YouTube Shorts title, <=90 chars")
    caption: str = Field(description="TikTok/Instagram caption, 1-2 sentences, may end with a question")
    hashtags: list[str] = Field(description="5-10 hashtags without the # symbol")
    trim_start_offset_s: float = Field(description="Seconds to trim from the start of the window (>=0)")
    trim_end_offset_s: float = Field(description="Seconds to trim from the end of the window (>=0)")
    reasoning: str = Field(description="One or two sentences explaining the score")


def _schema() -> dict:
    schema = ClipJudgement.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema["properties"].keys())
    return schema


def _image_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def build_context(trends: list[dict], best_posts: list[dict]) -> str:
    lines = ["## Trending GTA 6 shorts right now (title | views)"]
    for t in trends[:25]:
        lines.append(f"- {t['title']} | {t.get('views', 0):,}")
    if best_posts:
        lines.append("\n## This channel's best past posts (platform | views | hook | hashtags)")
        for p in best_posts:
            lines.append(f"- {p['platform']} | {p.get('views') or 0:,} | {p.get('hook')} | {p.get('hashtags')}")
    else:
        lines.append("\n(No performance history yet for this channel.)")
    return "\n".join(lines)


class Judge:
    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()

    def judge(
        self,
        frames: list[Path],
        duration_s: float,
        signal_score: float,
        context: str,
    ) -> ClipJudgement | None:
        content: list[dict] = [
            # Context first and cached: it is identical for every candidate in a run.
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        ]
        content += [_image_block(f) for f in frames]
        content.append({
            "type": "text",
            "text": (
                f"Candidate window: {duration_s:.1f}s long, {len(frames)} frames shown in order. "
                f"Audio/motion excitement score from signal analysis: {signal_score:.2f} "
                "(roughly -1 = calm, 0 = average, 2+ = very intense). Judge this clip."
            ),
        })

        response = self.client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={
                "effort": settings.claude_effort,
                "format": {"type": "json_schema", "schema": _schema()},
            },
            # Server-side fallback if the primary model declines a request.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        )

        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return ClipJudgement.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError):
            return None

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

SYSTEM_PROMPT = """You are a short-form video strategist for a GTA 6 clips channel that posts \
to TikTok, Instagram Reels and YouTube Shorts. The channel clips footage from creators who have \
given permission (clipping programs, direct agreements, Creative Commons), always credits them, \
and adds its own short commentary to every clip.

You are shown frames from a candidate moment (in order, evenly spaced) plus context: what GTA 6 \
topics are trending right now, which of the channel's own past posts performed best, and which \
creator and video the footage comes from.

Judge how likely this moment is to go viral as a vertical short. Weigh:
- Hook: is something visually striking or surprising in the first 1-3 seconds?
- Payoff: does it build to a clear moment (crash, escape, glitch, funny NPC, stunt, discovery)?
- Novelty: does it show something viewers have not seen a thousand times, or ride a current trend?
- Readability in 9:16: will the action still be clear on a phone?

Be honest: most moments are average. Reserve scores above 80 for clips you would bet on.

Write packaging in the voice of gaming TikTok: punchy and curiosity-driven. Rules:
- Nothing the clip does not deliver on, and no claims of being official Rockstar content.
- Never imply the channel recorded the footage; the creator is credited separately, so do not \
  add the credit yourself.
- Hook text is burned on screen: keep it under 45 characters.
- When a transcript is given, pick moments that make sense on their own: complete sentences and \
  a clear payoff. Set the trims so the clip starts at the beginning of a sentence and ends just \
  after the payoff line. Prefer moments where the creator is talking or reacting over stretches \
  where a trailer or gameplay simply plays with nobody speaking.
- In "campaign" style the clip keeps the creator's original audio with subtitles and no \
  voiceover: the headline (hook text) should say who the creator is and what happens, e.g. \
  "KAI REACTS TO THE MAP SIZE". Leave commentary as an empty string.
- In "commentary" style, the commentary is the channel's own original contribution. Write one or two spoken sentences \
  (max 28 words) that add something: context, a reaction, a detail viewers would miss, or a \
  question that invites comments. Do not just describe what is visibly happening."""


class ClipJudgement(BaseModel):
    virality_score: int = Field(description="0-100 likelihood this performs well as a short")
    keep: bool = Field(description="Whether this clip is worth rendering at all")
    category: str = Field(description="e.g. chase, crash, glitch, funny NPC, stunt, scenery, combat")
    hook_text: str = Field(description="On-screen text for the first 3 seconds, <=45 chars")
    title: str = Field(description="YouTube Shorts title, <=90 chars")
    caption: str = Field(description="TikTok/Instagram caption, 1-2 sentences, may end with a question")
    hashtags: list[str] = Field(description="5-10 hashtags without the # symbol")
    commentary: str = Field(description="1-2 spoken sentences of original commentary, <=28 words; "
                                        "empty string in campaign style")
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
        source_note: str = "",
        transcript: str = "",
        style: str = "commentary",
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
                "(roughly -1 = calm, 0 = average, 2+ = very intense). "
                + (f"Footage source: {source_note}. " if source_note else "")
                + f"Clip style: {style}. "
                + (f"\n\nWhat is said in this window (offsets from its start):\n{transcript}\n\n"
                   if transcript else "No transcript is available for this window. ")
                + "Judge this clip."
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

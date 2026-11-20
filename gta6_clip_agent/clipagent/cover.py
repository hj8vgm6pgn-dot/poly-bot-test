"""Cover images (thumbnails) in the channel's neon style.

A darkened frame from the clip, a glowing cyan frame, a large hollow pink tag
("GTA 6", or "TOP 3" for a compilation) and the clip's headline in white. Built
with ffmpeg only: the neon parts are drawn on black, blurred for the glow, and
screen-blended over the frame.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .edit import _escape_drawtext, _wrap

W, H = 1080, 1920
CYAN, PINK = "0x7df9ff", "0xff4fa3"


def make_cover(
    video: Path,
    at_s: float,
    headline: str,
    out_path: Path,
    font_file: str,
    tag: str = "GTA 6",
    channel: str = "ViceOnTheIce",
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    font = str(font_file).replace("\\", "/").replace(":", "\\:")

    def text(s: str, size: int, y: int, color: str = "white", border: int = 0, border_color: str = "black") -> str:
        b = f":borderw={border}:bordercolor={border_color}" if border else ""
        return (f"drawtext=fontfile='{font}':text='{_escape_drawtext(s)}':fontsize={size}:"
                f"fontcolor={color}{b}:x=(w-text_w)/2:y={y}")

    lines = _wrap(headline.upper(), width=15, max_lines=3)
    tag = tag.upper()[:8]
    tag_size = 300 if len(tag) <= 3 else (230 if len(tag) <= 5 else 170)
    box_top, tag_y = 560, 900
    head_y = tag_y + tag_size + 40
    box_h = max(820, head_y + len(lines) * 104 + 70 - box_top)

    neon = [f"drawbox=x=40:y={box_top}:w={W - 80}:h={box_h}:color={CYAN}:t=10",
            # hollow lettering: black fill disappears under the screen blend, the pink edge stays
            text(tag, tag_size, tag_y, color="black", border=11, border_color=PINK)]
    neon += [text(line, 88, head_y + i * 104) for i, line in enumerate(lines)]
    neon.append(f"drawtext=fontfile='{font}':text='{_escape_drawtext(channel)}':fontsize=40:"
                f"fontcolor=white:x=60:y=150")

    graph = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"eq=brightness=-0.22:saturation=1.15,gblur=sigma=3[base];"
        f"color=c=black:s={W}x{H},format=gbrp,{','.join(neon)},split=2[sharp][soft];"
        f"[soft]gblur=sigma=16[glow];"
        f"[base]format=gbrp[b0];[b0][glow]blend=all_mode=screen[b1];"
        f"[b1][sharp]blend=all_mode=screen,format=yuvj420p[out]"
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, at_s):.2f}", "-i", str(video),
         "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3", str(out_path)],
        check=True,
    )
    return out_path

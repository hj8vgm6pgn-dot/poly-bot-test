"""Cover images (thumbnails) in the channel's neon style.

Matches the profile picture: a purple-to-orange sunset wash over a darkened frame
from the clip, a glowing frame in the same purple-to-orange gradient, a large
hollow neon-pink tag ("GTA 6", or "TOP 3" for a compilation) and the clip's
headline in ice blue. Built with ffmpeg only: the neon parts are drawn on black,
blurred for the glow, and screen-blended over the frame.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .edit import _escape_drawtext, _wrap

W, H = 1080, 1920
PINK, ICE = "0xff4fa3", "0x9be8ff"
PURPLE_RGB, ORANGE_RGB = (162, 75, 255), (255, 154, 61)


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

    # hollow lettering: black fill disappears under the screen blend, the pink edge stays
    words = [text(tag, tag_size, tag_y, color="black", border=11, border_color=PINK)]
    words += [text(line, 88, head_y + i * 104, color=ICE) for i, line in enumerate(lines)]
    words.append(f"drawtext=fontfile='{font}':text='{_escape_drawtext(channel)}':fontsize=40:"
                 f"fontcolor=white:x=60:y=150")

    # Vertical purple (top) to orange (bottom) gradient, computed per pixel so it is identical every time.
    (r0, g0, b0), (r1, g1, b1) = PURPLE_RGB, ORANGE_RGB
    grad = (f"color=c=black:s={W}x{H},format=gbrp,"
            f"geq=r='{r0}+({r1 - r0})*Y/H':g='{g0}+({g1 - g0})*Y/H':b='{b0}+({b1 - b0})*Y/H'")
    graph = (
        f"{grad},split=2[gwash][gline];"
        # frame from the clip, darkened, with the sunset wash over it
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"eq=saturation=0.9,gblur=sigma=3,format=gbrp[frame];"
        f"[frame][gwash]blend=all_mode=normal:all_opacity=0.6,"
        f"lutrgb=r='val*0.55':g='val*0.55':b='val*0.55'[base];"
        # the gradient shows through a box-shaped mask, giving a purple-to-orange border
        f"color=c=black:s={W}x{H},format=gbrp,"
        f"drawbox=x=40:y={box_top}:w={W - 80}:h={box_h}:color=white:t=12[mask];"
        f"color=c=black:s={W}x{H},format=gbrp[blk];"
        f"[blk][gline][mask]maskedmerge[boxg];"
        f"color=c=black:s={W}x{H},format=gbrp,{','.join(words)}[txt];"
        f"[boxg][txt]blend=all_mode=screen,split=2[sharp][soft];"
        f"[soft]gblur=sigma=16[glow];"
        f"[base][glow]blend=all_mode=screen[b1];"
        f"[b1][sharp]blend=all_mode=screen,format=yuvj420p[out]"
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, at_s):.2f}", "-i", str(video),
         "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3", str(out_path)],
        check=True,
    )
    return out_path

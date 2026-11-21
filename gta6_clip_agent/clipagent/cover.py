"""Cover images (thumbnails) in the channel's retro-sunset neon style.

Built to match the profile picture: a deep navy-purple backdrop (the clip's frame,
darkened and tinted), a striped sunset disc fading from yellow through pink to
purple, a cyan neon tag with a white core ("GTA 6", or "TOP 3" for a compilation),
the clip's headline in ice blue, and a thin sunset-gradient frame around it all.

ffmpeg only, so it runs anywhere the agent does. Glows are a blurred copy of each
bright layer, screen-blended; the sharp lettering is laid over the top.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .edit import _escape_drawtext, _wrap

W, H = 1080, 1920
CYAN, CORE, ICE = "0x7df9ff", "0xf2ffff", "0x9be8ff"
YELLOW, PINK, PURPLE = (255, 209, 102), (255, 79, 163), (138, 63, 252)


def _sunset(channel: int, t: str) -> str:
    """Expression for one colour channel of the yellow -> pink -> purple gradient at position ``t`` (0..1)."""
    a, b, c = YELLOW[channel], PINK[channel], PURPLE[channel]
    return f"if(lt({t},0.55),{a}+({b - a})*({t})/0.55,{b}+({c - b})*(({t})-0.55)/0.45)"


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

    lines = _wrap(headline.upper(), width=15, max_lines=3)
    tag = tag.upper()[:8]
    tag_size = 300 if len(tag) <= 3 else (230 if len(tag) <= 5 else 170)

    cy, radius = 880, 330                       # sunset disc
    tag_y = cy - tag_size // 2 - 10
    head_y = cy + radius + 70
    box_top = cy - radius - 90
    box_bottom = head_y + len(lines) * 104 + 60

    def text(s: str, size: int, y: int, color: str, border: int = 0, border_color: str = "black") -> str:
        b = f":borderw={border}:bordercolor={border_color}" if border else ""
        return (f"drawtext=fontfile='{font}':text='{_escape_drawtext(s)}':fontsize={size}:"
                f"fontcolor={color}{b}:x=(w-text_w)/2:y={y}")

    def lettering(mask: bool) -> str:
        """All text, in colour, or in plain white to use as a cut-out mask."""
        pick = (lambda c: "white") if mask else (lambda c: c)
        parts = [text(tag, tag_size, tag_y, pick(CORE), border=12, border_color=pick(CYAN))]
        parts += [text(line, 88, head_y + i * 104, pick(ICE), border=3, border_color=pick("0x14092e"))
                  for i, line in enumerate(lines)]
        parts.append(f"drawtext=fontfile='{font}':text='{_escape_drawtext(channel)}':fontsize=40:"
                     f"fontcolor=white:x=60:y=150")
        return ",".join(parts)

    words, words_mask = lettering(False), lettering(True)

    # Sunset disc, drawn at half size and scaled up (faster, and it softens the edge).
    # Below the middle, dark bands of growing thickness cut through it.
    hx, hy, hr = W // 4, cy // 2, radius // 2
    t = f"clip((Y-{hy - hr})/{2 * hr},0,1)"
    disc = f"lt(hypot(X-{hx},Y-{hy}),{hr})"
    band = f"gt(Y,{hy + 12})*lt(mod(Y-{hy + 12},29),6+(Y-{hy + 12})/22)"
    lit = f"{disc}*(1-{band})"
    sun = (f"color=c=black:s={W // 2}x{H // 2},format=gbrp,"
           f"geq=r='{lit}*({_sunset(0, t)})':g='{lit}*({_sunset(1, t)})':b='{lit}*({_sunset(2, t)})',"
           f"scale={W}:{H}:flags=bilinear,format=gbrp")

    # Thin frame in the same gradient, top to bottom of the box.
    tb = f"clip((Y-{box_top})/{box_bottom - box_top},0,1)"
    grad = (f"color=c=black:s={W // 2}x{H // 2},format=gbrp,"
            f"geq=r='{_sunset(0, tb.replace('Y', '(2*Y)'))}':g='{_sunset(1, tb.replace('Y', '(2*Y)'))}':"
            f"b='{_sunset(2, tb.replace('Y', '(2*Y)'))}',scale={W}:{H}:flags=bilinear,format=gbrp")
    black = f"color=c=black:s={W}x{H},format=gbrp"

    graph = (
        # backdrop: the clip's frame, darkened and pushed toward deep navy-purple
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=4,format=gbrp,"
        f"lutrgb=r='val*0.22+16':g='val*0.14+8':b='val*0.30+34',vignette=PI/4.5,format=gbrp[base];"
        f"{sun},split=2[sun][sunsoft];[sunsoft]gblur=sigma=28[sunglow];"
        f"[base][sunglow]blend=all_mode=screen[b1];[b1][sun]blend=all_mode=screen[b2];"
        # gradient frame through a box-shaped mask
        f"{black},drawbox=x=40:y={box_top}:w={W - 80}:h={box_bottom - box_top}:color=white:t=8[mask];"
        f"{black}[blk];{grad}[g];[blk][g][mask]maskedmerge,split=2[line][linesoft];"
        f"[linesoft]gblur=sigma=12[lineglow];"
        f"[b2][lineglow]blend=all_mode=screen[b3];[b3][line]blend=all_mode=screen[b4];"
        # lettering: glow first, then the sharp text laid over the top
        f"{black},{words},gblur=sigma=14[textglow];[b4][textglow]blend=all_mode=screen[b5];"
        f"{black},{words}[text];{black},{words_mask}[textmask];"
        f"[b5][text][textmask]maskedmerge,format=yuvj420p[out]"
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, at_s):.2f}", "-i", str(video),
         "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3", str(out_path)],
        check=True,
    )
    return out_path

"""Render a candidate into a ready-to-post 1080x1920 vertical short with ffmpeg.

Layout: the 16:9 gameplay is scaled to full width in the middle, with a blurred,
zoomed copy filling the top and bottom (keeps all the action visible, unlike a
hard center-crop). Hook text sits at the top for the first few seconds, a short
fade-in/out smooths the loop, and audio is loudness-normalized to -14 LUFS
(what TikTok/Reels/Shorts normalize toward anyway).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

W, H = 1080, 1920


def _escape_drawtext(text: str) -> str:
    # drawtext uses ':' and '\'' as separators; escape them and strip newlines.
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def _wrap(text: str, width: int = 22) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:3]


def build_filter(hook: str, duration: float, font_file: str, mode: str = "blur") -> str:
    if mode == "crop":
        video = f"[0:v]scale=-2:{H},crop={W}:{H},setsar=1[base]"
    else:
        video = (
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale=-2:{H},crop={W}:{H},boxblur=20:2,eq=brightness=-0.08[bg];"
            f"[fgsrc]scale={W}:-2,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )

    chain = "[base]"
    filters = [video]
    hook_end = min(3.5, duration)
    for i, line in enumerate(_wrap(hook)):
        y = 260 + i * 105
        out = f"[t{i}]"
        filters.append(
            f"{chain}drawtext=fontfile='{font_file}':text='{_escape_drawtext(line)}':"
            f"fontsize=84:fontcolor=white:borderw=6:bordercolor=black:"
            f"x=(w-text_w)/2:y={y}:enable='between(t,0,{hook_end:.2f})'{out}"
        )
        chain = out

    fade_out = max(0.0, duration - 0.3)
    filters.append(f"{chain}fade=t=in:st=0:d=0.15,fade=t=out:st={fade_out:.2f}:d=0.3,format=yuv420p[v]")
    return ";".join(filters)


def render(
    source: Path,
    start_s: float,
    end_s: float,
    hook: str,
    out_path: Path,
    font_file: str,
    mode: str = "blur",
    has_audio: bool = True,
) -> Path:
    duration = end_s - start_s
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{start_s:.2f}", "-t", f"{duration:.2f}", "-i", str(source),
        "-filter_complex", build_filter(hook, duration, font_file, mode),
        "-map", "[v]",
    ]
    if has_audio:
        cmd += [
            "-map", "0:a:0",
            "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st={max(0, duration - 0.3):.2f}:d=0.3",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        ]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path

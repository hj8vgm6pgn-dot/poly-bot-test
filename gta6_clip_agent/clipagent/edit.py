"""Render a candidate into a ready-to-post 1080x1920 vertical short with ffmpeg.

Layout: the 16:9 gameplay is scaled to full width in the middle, with a blurred,
zoomed copy filling the top and bottom (keeps all the action visible, unlike a
hard center-crop). On top of that:

  * hook text at the top for the first few seconds
  * your commentary line as captions in the lower third (and spoken, if a
    voiceover file is supplied; the original audio is ducked underneath it)
  * a small persistent credit for the creator whose footage it is

A short fade-in/out smooths the loop, and audio is loudness-normalized to
-14 LUFS (what TikTok/Reels/Shorts normalize toward anyway).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

W, H = 1080, 1920
VO_START = 0.4  # seconds into the clip where the commentary begins


def _escape_drawtext(text: str) -> str:
    # drawtext uses ':' and '\'' as separators; escape them and strip newlines.
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def _wrap(text: str, width: int = 22, max_lines: int = 3) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def _caption_chunks(text: str, total_s: float, width: int = 26) -> list[tuple[float, float, list[str]]]:
    """Split commentary into two-line caption cards timed evenly across ``total_s``."""
    lines = _wrap(text, width=width, max_lines=40)
    cards = [lines[i:i + 2] for i in range(0, len(lines), 2)]
    if not cards:
        return []
    chars = [sum(len(l) for l in c) for c in cards]
    out, t = [], 0.0
    for card, n in zip(cards, chars):
        d = total_s * n / max(1, sum(chars))
        out.append((t, t + d, card))
        t += d
    return out


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def build_filter(
    hook: str,
    duration: float,
    font_file: str,
    mode: str = "blur",
    credit: str = "",
    commentary: str = "",
    commentary_s: float = 0.0,
    face: tuple[float, float, float, float] | None = None,
    subtitles: Path | None = None,
) -> str:
    half = H // 2
    if mode == "stack" and face:
        # Top: a 9:8 window around the creator's face. Bottom: the scene, centre-cropped to 9:8.
        fx, fy, fw, fh = face
        cw = min(1.0, max(fw * 3.2, 0.3))                       # fraction of frame width
        cx = min(max(fx + fw / 2 - cw / 2, 0.0), 1.0 - cw)
        ccy = fy + fh / 2
        video = (
            f"[0:v]split=2[facesrc][scenesrc];"
            f"[facesrc]crop=w='min(iw*{cw:.4f},ih*9/8)':h='min(iw*{cw:.4f},ih*9/8)*8/9':"
            f"x='min(max(iw*{cx:.4f},0),iw-ow)':y='min(max(ih*{ccy:.4f}-oh/2,0),ih-oh)',"
            f"scale={W}:{half},setsar=1[top];"
            f"[scenesrc]crop=w='min(iw,ih*9/8)':h='min(ih,iw*8/9)',scale={W}:{half},setsar=1[bot];"
            f"[top][bot]vstack=inputs=2[base]"
        )
    elif mode == "crop":
        # Centre the 9:16 window on the creator's face when we know where it is.
        cx = (face[0] + face[2] / 2) if face else 0.5
        video = (f"[0:v]scale=-2:{H},crop={W}:{H}:"
                 f"x='min(max(iw*{cx:.4f}-{W}/2,0),iw-{W})':y=0,setsar=1[base]")
    else:
        video = (
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale=-2:{H},crop={W}:{H},boxblur=20:2,eq=brightness=-0.08[bg];"
            f"[fgsrc]scale={W}:-2,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )

    chain, n = "[base]", 0
    filters = [video]
    # Windows paths contain a drive colon, which the filter syntax needs escaped.
    font_arg = str(font_file).replace("\\", "/").replace(":", "\\:")

    def draw(text: str, size: int, y: str, enable: str | None, alpha: float = 1.0, border: int = 6,
             box: bool = False) -> None:
        nonlocal chain, n
        out = f"[t{n}]"
        n += 1
        en = f":enable='{enable}'" if enable else ""
        style = (":box=1:boxcolor=black@0.82:boxborderw=20" if box
                 else f":borderw={border}:bordercolor=black@{alpha}")
        filters.append(
            f"{chain}drawtext=fontfile='{font_arg}':text='{_escape_drawtext(text)}':"
            f"fontsize={size}:fontcolor=white@{alpha}{style}:"
            f"x=(w-text_w)/2:y={y}{en}{out}"
        )
        chain = out

    if mode == "stack" and face:
        # Headline banner across the seam, kept on screen for the whole clip.
        lines = _wrap(hook.upper(), width=24, max_lines=2)
        y0 = half - (len(lines) * 84) // 2
        for i, line in enumerate(lines):
            draw(line, 52, str(y0 + i * 84), None, box=True)
    else:
        hook_end = min(3.5, duration)
        for i, line in enumerate(_wrap(hook, width=18)):
            draw(line, 84, str(260 + i * 105), f"between(t,0,{hook_end:.2f})")

    if subtitles:
        esc = str(subtitles).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        fonts = str(Path(font_file).parent).replace("\\", "/").replace(":", "\\:")
        out = f"[t{n}]"
        n += 1
        filters.append(f"{chain}ass=filename='{esc}':fontsdir='{fonts}'{out}")
        chain = out

    if commentary:
        span = min(commentary_s or max(3.0, len(commentary) / 14), max(1.0, duration - VO_START - 0.3))
        for start, end, card in _caption_chunks(commentary, span):
            for i, line in enumerate(card):
                draw(line, 60, str(1330 + i * 78),
                     f"between(t,{VO_START + start:.2f},{VO_START + end:.2f})", border=5)

    if credit:
        # Kept above the area the apps cover with their own caption/buttons.
        draw(credit[:40], 38, str(H - 330), None, alpha=0.85, border=3)

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
    credit: str = "",
    commentary: str = "",
    voiceover: Path | None = None,
    face: tuple[float, float, float, float] | None = None,
    captions: list[tuple[float, float, str]] | None = None,
) -> Path:
    """``mode``: blur | crop | stack. ``captions``: (start, end, text) cards of the creator's speech."""
    from .transcribe import write_ass

    duration = end_s - start_s
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vo_s = _duration(voiceover) if voiceover else 0.0
    if mode == "stack" and not face:
        mode = "blur"
    ass = None
    if captions:
        # Below the banner in the stacked layout, lower third otherwise.
        from .config import settings

        ass = write_ass(captions, out_path.with_suffix(".ass"), y=1090 if mode == "stack" else 1330,
                        font=settings.subtitle_font)
    vf = build_filter(hook, duration, font_file, mode, credit, commentary, vo_s, face, ass)

    cmd = ["ffmpeg", "-v", "error", "-y",
           "-ss", f"{start_s:.2f}", "-t", f"{duration:.2f}", "-i", str(source)]
    fade = f"afade=t=out:st={max(0, duration - 0.3):.2f}:d=0.3"
    af = None
    if voiceover:
        cmd += ["-i", str(voiceover)]
        delay = int(VO_START * 1000)
        vo = f"[1:a]aresample=48000,adelay={delay}|{delay},volume=1.6,apad[vo]"
        if has_audio:
            duck_end = VO_START + vo_s + 0.2
            af = (f"[0:a]aresample=48000,loudnorm=I=-14:TP=-1.5:LRA=11,"
                  f"volume=0.25:enable='between(t,{VO_START - 0.1:.2f},{duck_end:.2f})'[bg];"
                  f"{vo};[bg][vo]amix=inputs=2:duration=first:normalize=0,{fade}[a]")
        else:
            af = f"{vo};[vo]atrim=0:{duration:.2f},{fade}[a]"
    elif has_audio:
        af = f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,{fade}[a]"

    cmd += ["-filter_complex", vf + (";" + af if af else ""), "-map", "[v]"]
    if af:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
        "-t", f"{duration:.2f}", "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    if ass:
        ass.unlink(missing_ok=True)
    return out_path


def compile_segments(
    segments: list[dict],
    out_path: Path,
    font_file: str,
) -> Path:
    """Render several moments and join them into one video.

    Each segment is a dict of ``render`` keyword arguments (source, start_s, end_s,
    hook, has_audio, credit, commentary, voiceover). Used for 60s+ cuts: TikTok's
    Creator Rewards only pays on videos longer than a minute. Each segment keeps
    its own creator credit on screen.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parts_dir = out_path.parent / f".{out_path.stem}_parts"
    parts_dir.mkdir(exist_ok=True)
    parts = []
    for i, seg in enumerate(segments):
        part = parts_dir / f"p{i:02d}.mp4"
        render(out_path=part, font_file=font_file, **seg)
        if not (seg.get("has_audio", True) or seg.get("voiceover")):  # concat needs matching streams
            silent = parts_dir / f"p{i:02d}_a.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(part), "-f", "lavfi",
                 "-i", "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "160k", str(silent)], check=True)
            part = silent
        parts.append(part)
    listing = parts_dir / "list.txt"
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart", str(out_path)],
        check=True,
    )
    for p in parts_dir.iterdir():
        p.unlink()
    parts_dir.rmdir()
    return out_path

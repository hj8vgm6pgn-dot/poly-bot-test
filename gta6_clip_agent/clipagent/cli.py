"""Command-line entry point.

Sources (who you are allowed to clip):
    python -m clipagent sources add @creator --permission clipping_program --proof <link> [--payout 1.5]
    python -m clipagent sources add @creator --permission direct --proof <email/DM screenshot path>
    python -m clipagent sources list | disable <id> | enable <id>

Footage:
    python -m clipagent discover [--cc]     # list GTA 6 videos from approved channels (+ Creative Commons)
    python -m clipagent videos              # show what was found
    python -m clipagent fetch --top 3       # download from approved sources only
    python -m clipagent import clip.mp4 --source 2   # footage a creator sent you directly

Clips:
    python -m clipagent trends              # pull trending GTA 6 shorts metadata
    python -m clipagent scan [--no-ai]      # find + score moments
    python -m clipagent render [--min 60]   # 9:16 render with credit, captions, voiceover
    python -m clipagent script              # commentary lines to record in your own voice
    python -m clipagent voice-test me.m4a --pitch -3   # hear the pitch effect
    python -m clipagent render --ids 12     # re-render a clip with your recording
    python -m clipagent compile             # 60s+ cut of top moments (TikTok rewards)
    python -m clipagent review              # list rendered clips awaiting approval
    python -m clipagent approve 3 5 7       # or: reject 4
    python -m clipagent publish [--platforms youtube,tiktok,instagram] [--dry-run]
    python -m clipagent metrics             # refresh views/likes for posted clips
    python -m clipagent accounts            # connection check + monetization progress
    python -m clipagent run                 # trends + discover + fetch + scan + render

Nothing is fetched from a channel that is not an approved source, and nothing is
posted without an explicit `approve` first.
"""
from __future__ import annotations

import argparse
import json
import logging
import tempfile
from pathlib import Path

from . import analyze, cover, edit, layout, sources, transcribe, trends, voice
from .config import settings
from .db import DB
from .sources import PermissionError_

log = logging.getLogger("clipagent")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def _style(src: dict) -> str:
    """campaign = original audio + subtitles; commentary = our captions + voiceover."""
    return src.get("style") or ("campaign" if src["permission"] == "clipping_program" else "commentary")


def _transcript(video: dict | None, no_speech: bool = False, cached_only: bool = False) -> list | None:
    if no_speech or not video or not video.get("local_path"):
        return None
    cache = settings.output_dir / "transcripts" / f"video_{video['id']}.json"
    if cached_only:  # rendering reuses what the scan produced; it never starts a transcription
        return json.loads(cache.read_text()) if cache.exists() else None
    return transcribe.transcribe(Path(video["local_path"]), cache)


# --------------------------------------------------------------------------- sources
def cmd_sources(db: DB, args) -> None:
    if args.action == "list":
        rows = db.sources()
        if not rows:
            print("No approved sources yet. Add one with `sources add` (see SETUP.md for how to get permission).")
        for s in rows:
            problem = sources.permission_problem(s)
            views, payout = db.source_earnings(s["id"])
            state = "OK" if not problem else f"BLOCKED: {problem}"
            print(f"#{s['id']:<3} {s['name']} ({s['platform']}, {s['permission']}, {_style(s)} style)  {state}")
            print(f"      credit: {s['credit']}  proof: {s['proof'] or '-'}  expires: {s['expires_at'] or '-'}"
                  + ("  name allowed in headlines" if s.get("name_in_headline") else ""))
            if s["payout_per_1k"]:
                print(f"      payout: {s['payout_per_1k']}/1k views  posted views: {views:,}  est. earned: {payout:.2f}")
            if s["rules"]:
                print(f"      rules: {s['rules']}")
        return
    if args.action in ("disable", "enable"):
        db.update_source(int(args.ref), active=1 if args.action == "enable" else 0)
        print(f"source #{args.ref}: {args.action}d")
        return

    # add
    if args.permission == "own":
        info = {"channel_id": "own", "name": "My recordings", "handle": ""}
        platform = "own"
    elif args.platform == "youtube":
        info = sources.resolve_youtube_channel(args.ref)
        platform = "youtube"
    else:
        # Twitch/Kick/etc: the id must match what the platform reports as the uploader.
        info = {"channel_id": args.ref.lstrip("@"), "name": args.ref.lstrip("@"), "handle": args.ref}
        platform = args.platform
    if args.permission in ("clipping_program", "direct") and not args.proof:
        raise SystemExit("--proof is required: a link or file showing the creator's permission "
                         "(campaign page, email, DM screenshot).")
    if args.permission == "creative_commons":
        raise SystemExit("Creative Commons sources are added automatically by `discover --cc`, "
                         "one video at a time, after the licence is checked.")
    handle = info["handle"] if str(info["handle"]).startswith("@") else f"@{info['handle']}".rstrip("@")
    sid = db.add_source(
        name=info["name"], platform=platform, channel_id=info["channel_id"], handle=handle,
        permission=args.permission, proof=args.proof or "", credit=args.credit or handle or info["name"],
        required_tags=json.dumps([t.strip().lstrip("#") for t in (args.tags or "").split(",") if t.strip()]),
        rules=args.rules or "", payout_per_1k=args.payout, expires_at=args.expires, active=1,
        style=args.style, name_in_headline=1 if args.name_in_headline else 0,
    )
    print(f"Approved source #{sid}: {info['name']} ({args.permission})")


def cmd_discover(db: DB, args) -> None:
    total = 0
    for s in db.sources():
        if s["platform"] != "youtube" or s["permission"] not in ("clipping_program", "direct"):
            continue
        problem = sources.permission_problem(s)
        if problem:
            print(f"skip {s['name']}: {problem}")
            continue
        n = sources.discover_channel(db, s)
        print(f"{s['name']}: {n} GTA 6 videos")
        total += n
    if args.cc:
        for term in settings.search_terms:
            if term.strip():
                n = sources.discover_creative_commons(db, term.strip())
                print(f"Creative Commons '{term.strip()}': {n} videos")
                total += n
        print("Check Creative Commons results before using them: some uploaders mark videos "
              "CC that they do not own.")
    print(f"{total} videos discovered. See them with `videos`, download with `fetch`.")


def cmd_videos(db: DB, _args) -> None:
    for v in db.videos():
        s = db.source(v["source_id"]) or {}
        mins = f"{v['duration_s'] / 60:5.1f}m" if v["duration_s"] else "    ?"
        print(f"#{v['id']:<4} [{v['status']:<10}] {v['views'] or 0:>10,} views {mins}  "
              f"{s.get('name', '?')}: {v['title'][:60]}")


def cmd_fetch(db: DB, args) -> None:
    if args.ids:
        vids = [v for v in (db.video(i) for i in args.ids) if v]
    else:
        vids = db.videos("discovered")[: args.top]
    if not vids:
        print("Nothing to fetch. Run `discover` first.")
        return
    for v in vids:
        try:
            path = sources.fetch(db, v, max_minutes=args.max_minutes)
            print(f"#{v['id']} downloaded -> {path}")
        except PermissionError_ as e:
            db.update_video(v["id"], status="skipped")
            print(f"#{v['id']} BLOCKED: {e}")
        except RuntimeError as e:
            print(f"#{v['id']} failed: {e}")


def cmd_import(db: DB, args) -> None:
    src = db.source(args.source)
    try:
        vid = sources.import_file(db, Path(args.file), src, title=args.title or "", url=args.url or "")
    except PermissionError_ as e:
        raise SystemExit(f"BLOCKED: {e}")
    print(f"Registered as video #{vid} from {src['name']}")


# --------------------------------------------------------------------------- clips
def cmd_trends(db: DB, _args) -> None:
    n = trends.collect(db)
    print(f"Stored {n} trend items. Top 10:")
    for t in db.top_trends(10):
        print(f"  [{t['platform']}] {t['views']:>10,}  {t['title'][:80]}")


def cmd_scan(db: DB, args) -> None:
    vids = db.videos("downloaded") + (db.videos("scanned") if args.rescan else [])
    known = {str(Path(v["local_path"]).resolve()) for v in db.videos() if v["local_path"]}
    stray = [p for p in settings.footage_dir.rglob("*")
             if p.suffix.lower() in VIDEO_EXT and str(p.resolve()) not in known]
    for p in stray:
        print(f"skip {p}: not linked to an approved source (use `import {p} --source <id>`)")
    if not vids:
        print("No footage to scan. Use `fetch` or `import` first.")
        return

    judge, context = None, ""
    if not args.no_ai:
        from .score import Judge, build_context

        judge = Judge()
        context = build_context(db.top_trends(25), db.best_posts(10))

    for v in vids:
        src = db.source(v["source_id"])
        problem = sources.permission_problem(src)
        if problem:
            print(f"skip #{v['id']}: {problem}")
            continue
        f = Path(v["local_path"])
        if not f.exists():
            print(f"skip #{v['id']}: file missing ({f})")
            continue
        print(f"scanning #{v['id']} {f} ({src['name']}) ...")
        required = json.loads(src.get("required_tags") or "[]")
        credit = sources.credit_line(src, v)
        style = _style(src)
        lo, hi = ((settings.campaign_min_s, settings.campaign_max_s) if style == "campaign"
                  else (settings.min_clip_s, settings.max_clip_s))
        words = _transcript(v, args.no_speech)
        speech = None
        if words:
            n = max(1, int(analyze.probe_duration(f) * analyze.FPS))
            speech = transcribe.speech_rate(words, n, analyze.FPS)
        cands = analyze.find_candidates(f, lo, hi, settings.candidates_per_video, speech=speech)
        for c in cands:
            if words:
                c.start_s, c.end_s = transcribe.snap_to_speech(words, c.start_s, c.end_s)
            row = dict(source=str(f), start_s=c.start_s, end_s=c.end_s, signal_score=c.score,
                       source_id=src["id"], video_id=v["id"], credit=credit,
                       hook="", title="", caption="", hashtags="[]", voiceover="")
            if judge:
                with tempfile.TemporaryDirectory() as tmp:
                    frames = analyze.extract_frames(f, c.start_s, c.end_s, 8, Path(tmp))
                    j = judge.judge(
                        frames, c.duration, c.score, context,
                        source_note=f"creator {src['name']}, video \"{v['title']}\"",
                        transcript=transcribe.text_in(words, c.start_s, c.end_s, with_times=True) if words else "",
                        style=style, name_allowed=bool(src.get("name_in_headline")))
                if j is None:
                    print(f"  {c.start_s:7.1f}-{c.end_s:7.1f}s  (model gave no usable verdict)")
                    continue
                start = c.start_s + max(0.0, j.trim_start_offset_s)
                end = c.end_s - max(0.0, j.trim_end_offset_s)
                if end - start < 6:  # ignore trims that would gut the clip
                    start, end = c.start_s, c.end_s
                if words:
                    start, end = transcribe.snap_to_speech(words, start, end, max_shift=1.0)
                tags = list(dict.fromkeys([h.lstrip("#") for h in j.hashtags] + required))
                row.update(start_s=start, end_s=end, ai_score=j.virality_score,
                           hook=j.hook_text, title=j.title, caption=j.caption,
                           hashtags=json.dumps(tags),
                           voiceover="" if style == "campaign" else j.commentary,
                           reasoning=f"[{j.category}] {j.reasoning}",
                           status="candidate" if j.keep else "rejected")
                print(f"  {start:7.1f}-{end:7.1f}s  score {j.virality_score:3d}  {j.category:<12} {j.hook_text}")
            else:
                tags = list(dict.fromkeys(["gta6", "gtavi", "gaming"] + required))
                row.update(ai_score=None, hook=(f"{src['name']} on GTA 6" if src.get("name_in_headline") else "GTA 6 reaction")
                           if style == "campaign" else "Wait for it...",
                           title="GTA 6 moment #Shorts",
                           caption="GTA 6 is wild", hashtags=json.dumps(tags))
                print(f"  {c.start_s:7.1f}-{c.end_s:7.1f}s  signal {c.score:5.2f}")
            db.add_clip(**row)
        db.update_video(v["id"], status="scanned")


def _render_kwargs(db: DB, c: dict, vo_dir: Path, with_voice: bool = True, mode: str = "auto") -> dict:
    """Everything `edit.render` needs for one clip, after re-checking the permission."""
    src = db.source(c["source_id"])
    sources.require(src)
    path = Path(c["source"])
    campaign = _style(src) == "campaign"

    # Subtitles of the creator's own words (campaign style keeps their audio front and centre).
    words = _transcript(db.video(c["video_id"]), cached_only=True) if c.get("video_id") else None
    captions = transcribe.caption_cards(words, c["start_s"], c["end_s"]) if words else None

    commentary = "" if campaign else (c.get("voiceover") or "")
    vo, vo_from = None, "none"
    if with_voice and commentary:
        vo, vo_from = voice.voiceover_for(c["id"], commentary, vo_dir)

    face = layout.detect_face(path, c["start_s"], c["end_s"]) if mode in ("auto", "stack", "crop") else None
    chosen = layout.choose(face) if mode == "auto" else mode
    return dict(source=path, start_s=c["start_s"], end_s=c["end_s"], hook=c["hook"] or "",
                has_audio=analyze.has_audio(path), credit=sources.watermark(src),
                commentary=commentary, voiceover=vo, mode=chosen, face=face, _voice=vo_from,
                # Our commentary captions and the creator's subtitles share the lower third.
                captions=None if commentary else captions)


def _cover_path(clip_id: int) -> Path:
    return settings.output_dir / "covers" / f"clip_{clip_id:05d}.jpg"


def _make_cover(c: dict, tag: str = "GTA 6") -> None:
    """Neon-style cover from a frame 40% of the way into the clip."""
    try:
        at = c["start_s"] + 0.4 * (c["end_s"] - c["start_s"])
        cover.make_cover(Path(c["source"]), at, c["hook"] or c["title"] or "GTA 6", _cover_path(c["id"]),
                         settings.font_file, tag=tag)
    except Exception as e:  # a missing cover should never stop a render
        log.warning("cover for #%s failed: %s", c["id"], e)


def cmd_render(db: DB, args) -> None:
    rendered_dir = settings.output_dir / "rendered"
    if args.ids:
        # Re-render specific clips, e.g. after recording their voiceover.
        cands = [c for c in (db.clip(i) for i in args.ids)
                 if c and c["source"] != "compilation" and c["status"] != "published"]
    else:
        cands = db.clips("candidate")
        if args.min is not None:
            cands = [c for c in cands if (c["ai_score"] or 0) >= args.min]
        cands = cands[: args.limit]
    if not cands:
        print("Nothing to render. Run `scan` first (or lower --min).")
        return
    for c in cands:
        out = rendered_dir / f"clip_{c['id']:05d}.mp4"
        try:
            kw = _render_kwargs(db, c, settings.output_dir / "voice", with_voice=not args.no_voice,
                                mode=args.mode)
        except PermissionError_ as e:
            print(f"#{c['id']} BLOCKED: {e}")
            continue
        vo_from = kw.pop("_voice")
        note = {"recording": "your voice", "tts": "synthetic voice", "none": "captions only"}[vo_from]
        if not kw["commentary"]:
            note = "original audio"
        print(f"rendering #{c['id']} ({kw['mode']} layout, {note}) -> {out}")
        edit.render(out_path=out, font_file=settings.font_file, **kw)
        if not args.no_cover:
            _make_cover(c)
        db.update_clip(c["id"], rendered_path=str(out),
                       status="approved" if c["status"] == "approved" else "rendered")


def cmd_compile(db: DB, args) -> None:
    pool = [c for c in db.clips("candidate") + db.clips("rendered")
            if c["source"] != "compilation" and (args.min is None or (c["ai_score"] or 0) >= args.min)
            and not sources.permission_problem(db.source(c["source_id"]))]
    pool.sort(key=lambda c: (c["ai_score"] or 0, c["signal_score"] or 0), reverse=True)
    chosen, total = [], 0.0
    for c in pool:
        dur = c["end_s"] - c["start_s"]
        if total + dur > args.max_seconds:
            continue
        chosen.append(c)
        total += dur
        if total >= args.min_seconds:
            break
    if total < args.min_seconds:
        print(f"Only {total:.0f}s of usable moments (need {args.min_seconds:.0f}s). Scan more footage.")
        return
    # Best moment first: it carries the hook and decides whether people keep watching.
    lead = chosen[0]
    segments = []
    for i, c in enumerate(chosen):
        kw = _render_kwargs(db, c, settings.output_dir / "voice", with_voice=not args.no_voice,
                            mode=args.mode)
        kw.pop("_voice")
        if i and kw["mode"] != "stack":  # the stacked layout keeps its own headline per segment
            kw["hook"] = ""
        segments.append(kw)
    credits = list(dict.fromkeys(c["credit"] for c in chosen if c.get("credit")))
    tags = list(dict.fromkeys(t for c in chosen for t in json.loads(c["hashtags"] or "[]")))
    cid = db.add_clip(source="compilation", start_s=0.0, end_s=total,
                      signal_score=sum(c["signal_score"] or 0 for c in chosen) / len(chosen),
                      ai_score=lead["ai_score"], hook=lead["hook"], title=lead["title"],
                      caption=lead["caption"], hashtags=json.dumps(tags[:15]),
                      source_id=lead["source_id"], credit="\n".join(credits),
                      reasoning="compilation of clips " + ", ".join(f"#{c['id']}" for c in chosen))
    out = settings.output_dir / "rendered" / f"clip_{cid:05d}.mp4"
    print(f"compiling {len(chosen)} moments ({total:.0f}s) -> {out}")
    edit.compile_segments(segments, out, settings.font_file)
    _make_cover({**lead, "id": cid}, tag=f"TOP {len(chosen)}")
    db.update_clip(cid, rendered_path=str(out), status="rendered")


def cmd_script(db: DB, _args) -> None:
    """List the commentary lines to record in your own voice."""
    rows = [c for c in db.clips("candidate") + db.clips("rendered") + db.clips("approved")
            if c.get("voiceover") and c["source"] != "compilation"]
    if not rows:
        print("No commentary lines yet. Run `scan` on a source with --style commentary.")
        return
    settings.voice_dir.mkdir(parents=True, exist_ok=True)
    print(f"Record each line, save it in {settings.voice_dir}/ under the name shown, then run "
          f"`render --ids <id>`.\nPitch shift: {settings.voice_pitch:+g} semitones (VOICE_PITCH).\n")
    for c in sorted(rows, key=lambda c: c["id"]):
        rec = voice.recording_for(c["id"])
        state = f"recorded ({rec.name})" if rec else f"save as clip_{c['id']:05d}.m4a"
        print(f"#{c['id']:<4} [{state}]\n       \"{c['voiceover']}\"")


def cmd_voice_test(_db: DB, args) -> None:
    """Process one recording so you can hear the pitch setting before using it."""
    out = settings.output_dir / "voice_test"
    try:
        res = voice.process_recording(Path(args.file), out, semitones=args.pitch, preset=args.preset)
    except RuntimeError as e:
        raise SystemExit(str(e))
    name = args.preset or (settings.voice_preset if args.pitch is None else None)
    if name:
        print(f"Wrote {res} with the '{name}' effect. To keep it, set VOICE_PRESET={name} in .env.")
    else:
        used = settings.voice_pitch if args.pitch is None else args.pitch
        print(f"Wrote {res} at {used:+g} semitones. To keep it, set VOICE_PITCH in .env.")


def cmd_review(db: DB, _args) -> None:
    rows = db.clips("rendered") + db.clips("approved")
    if not rows:
        print("No rendered clips. Run `render` first.")
        return
    for c in rows:
        score = "--" if c["ai_score"] is None else f"{c['ai_score']:.0f}"
        src = db.source(c["source_id"]) or {}
        print(f"#{c['id']:<4} [{c['status']:<8}] score {score:>3}  {c['end_s'] - c['start_s']:4.1f}s  {c['rendered_path']}")
        print(f"       from: {src.get('name', '?')} ({src.get('permission', '?')})")
        if _cover_path(c["id"]).exists():
            print(f"       cover: {_cover_path(c['id'])}")
        print(f"       hook: {c['hook']}\n       title: {c['title']}")
        if c.get("voiceover"):
            print(f"       commentary: {c['voiceover']}")
        print(f"       why: {c['reasoning'] or '-'}")


def cmd_set_status(db: DB, ids: list[int], status: str) -> None:
    for i in ids:
        if not db.clip(i):
            print(f"#{i}: not found")
            continue
        db.update_clip(i, status=status)
        print(f"#{i}: {status}")


def cmd_publish(db: DB, args) -> None:
    from .publish import publish_reel, upload_draft, upload_short
    from .publish.common import full_caption

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    uploaders = {"youtube": lambda c: upload_short(c, privacy=args.yt_privacy),
                 "tiktok": upload_draft, "instagram": publish_reel}
    clips = db.clips("approved")
    if not clips:
        print("No approved clips. Use `review` and `approve <id>` first.")
        return
    for c in clips:
        # Permissions can lapse between rendering and posting; check again at the door.
        problem = sources.permission_problem(db.source(c["source_id"]))
        if problem:
            print(f"#{c['id']} BLOCKED: {problem}")
            continue
        for p in platforms:
            if args.dry_run:
                print(f"[dry-run] would post #{c['id']} to {p}: {c['title']}")
                continue
            try:
                ext_id = uploaders[p](c)
                db.add_post(c["id"], p, ext_id, "posted" if p != "tiktok" else "draft")
                print(f"#{c['id']} -> {p}: {ext_id}")
                if p == "tiktok":
                    print("   TikTok drafts carry no caption. Paste this when you post it:\n   "
                          + full_caption(c).replace("\n", "\n   "))
            except Exception as e:  # keep going on other platforms
                print(f"#{c['id']} -> {p} FAILED: {e}")
        if not args.dry_run:
            db.update_clip(c["id"], status="published")


def cmd_tiktok_login(_db: DB, args) -> None:
    from . import tiktok_auth

    try:
        url, state = tiktok_auth.authorize_url()
    except RuntimeError as e:
        raise SystemExit(str(e))
    print("1. Open this link in a browser where you are logged in to the channel's TikTok account:\n")
    print(f"   {url}\n")
    print("2. Approve the app. You will land on a page that shows a code.")
    print(f"   Check that the page shows this security check value: {state}")
    code = args.code or input("3. Paste the code here and press Enter: ")
    try:
        token = tiktok_auth.exchange_code(code)
    except (RuntimeError, Exception) as e:
        raise SystemExit(f"Sign-in failed: {e}")
    print(f"Signed in. Granted permissions: {token.get('scope', '?')}. Run `accounts` to confirm.")


def cmd_accounts(_db: DB, _args) -> None:
    from .accounts import all_statuses, format_status

    for st in all_statuses():
        print(format_status(st))
        print()


def cmd_metrics(db: DB, _args) -> None:
    from .publish.youtube import video_metrics

    rows = db.conn.execute("SELECT platform, external_id FROM posts").fetchall()
    tiktok_stats = {v["id"]: v for v in trends.tiktok_own_videos(50)}
    for r in rows:
        try:
            if r["platform"] == "youtube":
                m = video_metrics(r["external_id"])
            elif r["platform"] == "instagram":
                m = trends.instagram_media_metrics(r["external_id"])
            else:
                # Inbox uploads get a publish_id, not a video id; match manually-posted videos
                # by id once you know it, or rely on the own-videos list for learning.
                m = tiktok_stats.get(r["external_id"], {})
            if m:
                db.update_post_metrics(r["platform"], r["external_id"], m)
                print(f"{r['platform']:<9} {r['external_id']}: {m}")
        except Exception as e:
            print(f"{r['platform']} {r['external_id']}: failed ({e})")
    for s in db.sources():
        if s["payout_per_1k"]:
            views, payout = db.source_earnings(s["id"])
            print(f"{s['name']}: {views:,} views on your clips, est. program payout {payout:.2f}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="clipagent", description="GTA 6 viral short-form clip agent")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    so = sub.add_parser("sources", help="manage the creators you are allowed to clip")
    so.add_argument("action", choices=["add", "list", "disable", "enable"])
    so.add_argument("ref", nargs="?", help="@handle / channel URL / channel id (add), or source id")
    so.add_argument("--permission", choices=list(sources.PERMISSIONS), default="direct")
    so.add_argument("--platform", default="youtube", help="youtube (default), twitch, kick, ...")
    so.add_argument("--proof", help="link or file showing the permission")
    so.add_argument("--credit", help="how to credit them, e.g. @creator (default: their handle)")
    so.add_argument("--tags", help="comma-separated hashtags the creator/campaign requires")
    so.add_argument("--rules", help="campaign rules to remember")
    so.add_argument("--payout", type=float, help="clipping-program payout per 1,000 views")
    so.add_argument("--expires", help="date the permission ends, YYYY-MM-DD")
    so.add_argument("--name-in-headline", action="store_true",
                    help="allow the creator's name in headlines and titles (only if their rules require it)")
    so.add_argument("--style", choices=["campaign", "commentary"],
                    help="campaign: original audio + subtitles (default for clipping programs); "
                         "commentary: your captions + voiceover")

    d = sub.add_parser("discover")
    d.add_argument("--cc", action="store_true", help="also search Creative Commons GTA 6 videos")
    sub.add_parser("videos")
    f = sub.add_parser("fetch")
    f.add_argument("ids", type=int, nargs="*")
    f.add_argument("--top", type=int, default=3, help="fetch the N most-viewed discovered videos")
    f.add_argument("--max-minutes", type=float, default=90)
    im = sub.add_parser("import")
    im.add_argument("file")
    im.add_argument("--source", type=int, required=True)
    im.add_argument("--title")
    im.add_argument("--url")

    sub.add_parser("trends")
    s = sub.add_parser("scan")
    s.add_argument("--no-ai", action="store_true", help="signal analysis only, no Claude calls")
    s.add_argument("--rescan", action="store_true", help="re-scan videos already scanned")
    s.add_argument("--no-speech", action="store_true", help="skip speech-to-text")
    r = sub.add_parser("render")
    r.add_argument("--min", type=float, default=None, help="minimum AI score to render")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--ids", type=int, nargs="+", help="re-render these clips (e.g. after recording your voice)")
    r.add_argument("--mode", choices=["auto", "stack", "crop", "blur"], default="auto",
                   help="auto picks by finding the creator's face")
    r.add_argument("--no-voice", action="store_true", help="captions only, no spoken commentary")
    r.add_argument("--no-cover", action="store_true", help="skip the cover image")
    cp = sub.add_parser("compile")
    cp.add_argument("--min-seconds", type=float, default=65)
    cp.add_argument("--max-seconds", type=float, default=110)
    cp.add_argument("--min", type=float, default=None, help="minimum AI score of included moments")
    cp.add_argument("--mode", choices=["auto", "stack", "crop", "blur"], default="auto")
    cp.add_argument("--no-voice", action="store_true")
    sub.add_parser("script", help="list commentary lines to record in your own voice")
    vt = sub.add_parser("voice-test", help="hear your voice with the pitch effect")
    vt.add_argument("file")
    vt.add_argument("--pitch", type=float, default=None, help="semitones, e.g. -3 (deeper) to try")
    vt.add_argument("--preset", choices=list(voice.PRESETS), help="a named voice effect to try")
    sub.add_parser("review")
    a = sub.add_parser("approve")
    a.add_argument("ids", type=int, nargs="+")
    rj = sub.add_parser("reject")
    rj.add_argument("ids", type=int, nargs="+")
    p = sub.add_parser("publish")
    p.add_argument("--platforms", default="youtube,tiktok,instagram")
    p.add_argument("--yt-privacy", default="private", choices=["private", "unlisted", "public"])
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("metrics")
    sub.add_parser("accounts")
    tl = sub.add_parser("tiktok-login", help="sign in to your TikTok account")
    tl.add_argument("--code", help="the code from the callback page (skips the prompt)")
    run = sub.add_parser("run")
    run.add_argument("--no-ai", action="store_true")
    run.add_argument("--min", type=float, default=60)
    run.add_argument("--cc", action="store_true")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    settings.ensure_dirs()
    db = DB(settings.db_path)
    ns = argparse.Namespace

    if args.cmd == "sources":
        if args.action != "list" and not args.ref and args.permission != "own":
            ap.error("sources add/disable/enable needs a channel or source id")
        cmd_sources(db, args)
    elif args.cmd == "discover":
        cmd_discover(db, args)
    elif args.cmd == "videos":
        cmd_videos(db, args)
    elif args.cmd == "fetch":
        cmd_fetch(db, args)
    elif args.cmd == "import":
        cmd_import(db, args)
    elif args.cmd == "trends":
        cmd_trends(db, args)
    elif args.cmd == "scan":
        cmd_scan(db, args)
    elif args.cmd == "render":
        cmd_render(db, args)
    elif args.cmd == "compile":
        cmd_compile(db, args)
    elif args.cmd == "script":
        cmd_script(db, args)
    elif args.cmd == "voice-test":
        cmd_voice_test(db, args)
    elif args.cmd == "review":
        cmd_review(db, args)
    elif args.cmd == "approve":
        cmd_set_status(db, args.ids, "approved")
    elif args.cmd == "reject":
        cmd_set_status(db, args.ids, "rejected")
    elif args.cmd == "publish":
        cmd_publish(db, args)
    elif args.cmd == "metrics":
        cmd_metrics(db, args)
    elif args.cmd == "accounts":
        cmd_accounts(db, args)
    elif args.cmd == "tiktok-login":
        cmd_tiktok_login(db, args)
    elif args.cmd == "run":
        cmd_trends(db, args)
        cmd_discover(db, ns(cc=args.cc))
        cmd_fetch(db, ns(ids=[], top=3, max_minutes=90))
        cmd_scan(db, ns(no_ai=args.no_ai, rescan=False, no_speech=False))
        cmd_render(db, ns(min=None if args.no_ai else args.min, limit=10, mode="auto", no_voice=False, ids=None,
                          no_cover=False))
        cmd_review(db, args)


if __name__ == "__main__":
    main()

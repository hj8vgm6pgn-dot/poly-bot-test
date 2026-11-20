"""Command-line entry point.

    python -m clipagent trends              # pull trending GTA 6 shorts metadata
    python -m clipagent scan [--no-ai]      # find + score moments in footage/
    python -m clipagent render [--min 60]   # render the best candidates to 9:16
    python -m clipagent review              # list rendered clips awaiting approval
    python -m clipagent approve 3 5 7       # mark clips as approved (or: reject 4)
    python -m clipagent publish [--platforms youtube,tiktok,instagram] [--dry-run]
    python -m clipagent metrics             # refresh views/likes for posted clips
    python -m clipagent run                 # trends + scan + render in one go

Nothing is posted without an explicit `approve` first.
"""
from __future__ import annotations

import argparse
import json
import logging
import tempfile
from pathlib import Path

from . import analyze, edit, trends
from .config import settings
from .db import DB

log = logging.getLogger("clipagent")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def cmd_trends(db: DB, _args) -> None:
    n = trends.collect(db)
    print(f"Stored {n} trend items. Top 10:")
    for t in db.top_trends(10):
        print(f"  [{t['platform']}] {t['views']:>10,}  {t['title'][:80]}")


def cmd_scan(db: DB, args) -> None:
    files = sorted(p for p in settings.footage_dir.rglob("*") if p.suffix.lower() in VIDEO_EXT)
    if not files:
        print(f"No footage found in {settings.footage_dir}/ - drop your GTA 6 recordings there.")
        return
    already = {c["source"] for c in db.clips()}
    judge, context = None, ""
    if not args.no_ai:
        from .score import Judge, build_context

        judge = Judge()
        context = build_context(db.top_trends(25), db.best_posts(10))

    for f in files:
        if str(f) in already and not args.rescan:
            print(f"skip (already scanned): {f}")
            continue
        print(f"scanning {f} ...")
        cands = analyze.find_candidates(
            f, settings.min_clip_s, settings.max_clip_s, settings.candidates_per_video
        )
        for c in cands:
            row = dict(source=str(f), start_s=c.start_s, end_s=c.end_s, signal_score=c.score,
                       hook="", title="", caption="", hashtags="[]")
            if judge:
                with tempfile.TemporaryDirectory() as tmp:
                    frames = analyze.extract_frames(f, c.start_s, c.end_s, 8, Path(tmp))
                    j = judge.judge(frames, c.duration, c.score, context)
                if j is None:
                    print(f"  {c.start_s:7.1f}-{c.end_s:7.1f}s  (model gave no usable verdict)")
                    continue
                start = c.start_s + max(0.0, j.trim_start_offset_s)
                end = c.end_s - max(0.0, j.trim_end_offset_s)
                if end - start < 6:  # ignore trims that would gut the clip
                    start, end = c.start_s, c.end_s
                row.update(start_s=start, end_s=end, ai_score=j.virality_score,
                           hook=j.hook_text, title=j.title, caption=j.caption,
                           hashtags=json.dumps([h.lstrip("#") for h in j.hashtags]),
                           reasoning=f"[{j.category}] {j.reasoning}",
                           status="candidate" if j.keep else "rejected")
                print(f"  {start:7.1f}-{end:7.1f}s  score {j.virality_score:3d}  {j.category:<12} {j.hook_text}")
            else:
                row.update(ai_score=None, hook="Wait for it...", title="GTA 6 moment #Shorts",
                           caption="GTA 6 is wild", hashtags=json.dumps(["gta6", "gtavi", "gaming"]))
                print(f"  {c.start_s:7.1f}-{c.end_s:7.1f}s  signal {c.score:5.2f}")
            db.add_clip(**row)


def cmd_render(db: DB, args) -> None:
    rendered_dir = settings.output_dir / "rendered"
    cands = db.clips("candidate")
    if args.min is not None:
        cands = [c for c in cands if (c["ai_score"] or 0) >= args.min]
    cands = cands[: args.limit]
    if not cands:
        print("Nothing to render. Run `scan` first (or lower --min).")
        return
    for c in cands:
        out = rendered_dir / f"clip_{c['id']:05d}.mp4"
        print(f"rendering #{c['id']} -> {out}")
        edit.render(Path(c["source"]), c["start_s"], c["end_s"], c["hook"] or "", out,
                    settings.font_file, mode=args.mode, has_audio=analyze.has_audio(Path(c["source"])))
        db.update_clip(c["id"], rendered_path=str(out), status="rendered")


def cmd_review(db: DB, _args) -> None:
    rows = db.clips("rendered") + db.clips("approved")
    if not rows:
        print("No rendered clips. Run `render` first.")
        return
    for c in rows:
        score = "--" if c["ai_score"] is None else f"{c['ai_score']:.0f}"
        print(f"#{c['id']:<4} [{c['status']:<8}] score {score:>3}  {c['end_s'] - c['start_s']:4.1f}s  {c['rendered_path']}")
        print(f"       hook: {c['hook']}\n       title: {c['title']}\n       why: {c['reasoning'] or '-'}")


def cmd_set_status(db: DB, ids: list[int], status: str) -> None:
    for i in ids:
        if not db.clip(i):
            print(f"#{i}: not found")
            continue
        db.update_clip(i, status=status)
        print(f"#{i}: {status}")


def cmd_publish(db: DB, args) -> None:
    from .publish import publish_reel, upload_draft, upload_short

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    uploaders = {"youtube": lambda c: upload_short(c, privacy=args.yt_privacy),
                 "tiktok": upload_draft, "instagram": publish_reel}
    clips = db.clips("approved")
    if not clips:
        print("No approved clips. Use `review` and `approve <id>` first.")
        return
    for c in clips:
        for p in platforms:
            if args.dry_run:
                print(f"[dry-run] would post #{c['id']} to {p}: {c['title']}")
                continue
            try:
                ext_id = uploaders[p](c)
                db.add_post(c["id"], p, ext_id, "posted" if p != "tiktok" else "draft")
                print(f"#{c['id']} -> {p}: {ext_id}")
            except Exception as e:  # keep going on other platforms
                print(f"#{c['id']} -> {p} FAILED: {e}")
        if not args.dry_run:
            db.update_clip(c["id"], status="published")


def cmd_metrics(db: DB, _args) -> None:
    from .publish.youtube import video_metrics

    rows = db.conn.execute("SELECT platform, external_id FROM posts").fetchall()
    tiktok_stats = {v["id"]: v for v in trends.tiktok_own_videos(50)} if settings.tiktok_access_token else {}
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="clipagent", description="GTA 6 viral short-form clip agent")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("trends")
    s = sub.add_parser("scan")
    s.add_argument("--no-ai", action="store_true", help="signal analysis only, no Claude calls")
    s.add_argument("--rescan", action="store_true", help="re-scan files already in the DB")
    r = sub.add_parser("render")
    r.add_argument("--min", type=float, default=None, help="minimum AI score to render")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--mode", choices=["blur", "crop"], default="blur")
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
    run = sub.add_parser("run")
    run.add_argument("--no-ai", action="store_true")
    run.add_argument("--min", type=float, default=60)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    settings.ensure_dirs()
    db = DB(settings.db_path)

    if args.cmd == "trends":
        cmd_trends(db, args)
    elif args.cmd == "scan":
        cmd_scan(db, args)
    elif args.cmd == "render":
        cmd_render(db, args)
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
    elif args.cmd == "run":
        cmd_trends(db, args)
        cmd_scan(db, argparse.Namespace(no_ai=args.no_ai, rescan=False))
        cmd_render(db, argparse.Namespace(min=None if args.no_ai else args.min, limit=10, mode="blur"))
        cmd_review(db, args)


if __name__ == "__main__":
    main()

"""Tiny SQLite store for candidates, rendered clips, posts and their metrics."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY,
    platform TEXT, external_id TEXT, title TEXT, views INTEGER, likes INTEGER,
    comments INTEGER, published_at TEXT, tags TEXT, fetched_at REAL,
    UNIQUE(platform, external_id)
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    source TEXT, start_s REAL, end_s REAL, signal_score REAL,
    ai_score REAL, title TEXT, hook TEXT, caption TEXT, hashtags TEXT,
    reasoning TEXT, rendered_path TEXT,
    status TEXT DEFAULT 'candidate',   -- candidate | rendered | approved | rejected | published
    created_at REAL
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT, platform TEXT, channel_id TEXT, handle TEXT,
    permission TEXT,            -- clipping_program | direct | creative_commons | own
    proof TEXT,                 -- link/file showing the permission (campaign page, email, DM screenshot)
    credit TEXT,                -- how to credit, e.g. "@creator"
    required_tags TEXT,         -- JSON list of hashtags the creator/campaign requires
    rules TEXT,                 -- free-text campaign rules
    payout_per_1k REAL,         -- clipping-program payout per 1,000 views, if any
    expires_at TEXT,            -- ISO date the permission ends, if any
    active INTEGER DEFAULT 1,
    created_at REAL,
    UNIQUE(platform, channel_id)
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES sources(id),
    platform TEXT, external_id TEXT, url TEXT, title TEXT, license TEXT,
    views INTEGER, published_at TEXT, duration_s REAL, local_path TEXT,
    status TEXT DEFAULT 'discovered',   -- discovered | downloaded | scanned | skipped
    UNIQUE(platform, external_id)
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    clip_id INTEGER REFERENCES clips(id), platform TEXT, external_id TEXT,
    status TEXT, posted_at REAL,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, metrics_at REAL
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(clips)")}
        for col, typ in (("source_id", "INTEGER"), ("video_id", "INTEGER"),
                         ("credit", "TEXT"), ("voiceover", "TEXT")):
            if col not in have:
                self.conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {typ}")
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(sources)")}
        if "style" not in have:  # campaign | commentary
            self.conn.execute("ALTER TABLE sources ADD COLUMN style TEXT")
        for col in ("overlay_path", "overlay_pos", "cta", "caption_required"):  # campaign-required logo and call to action
            if col not in have:
                self.conn.execute(f"ALTER TABLE sources ADD COLUMN {col} TEXT")
        if "plain_cover" not in have:  # 1 = campaign forbids logos / templated thumbnails
            self.conn.execute("ALTER TABLE sources ADD COLUMN plain_cover INTEGER DEFAULT 0")
        if "name_in_headline" not in have:  # 1 = this source's rules require the creator's name
            self.conn.execute("ALTER TABLE sources ADD COLUMN name_in_headline INTEGER DEFAULT 0")
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # --- trends -----------------------------------------------------------
    def upsert_trend(self, platform: str, item: dict) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO trends(platform, external_id, title, views, likes, comments,
                                      published_at, tags, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(platform, external_id) DO UPDATE SET
                     views=excluded.views, likes=excluded.likes, comments=excluded.comments,
                     fetched_at=excluded.fetched_at""",
                (platform, item["id"], item.get("title", ""), item.get("views", 0),
                 item.get("likes", 0), item.get("comments", 0), item.get("published_at", ""),
                 json.dumps(item.get("tags", [])), time.time()),
            )

    def top_trends(self, limit: int = 25) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trends ORDER BY views DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- sources ----------------------------------------------------------
    def add_source(self, **fields) -> int:
        fields.setdefault("created_at", time.time())
        cols, qs = ",".join(fields), ",".join("?" for _ in fields)
        with self.tx() as c:
            cur = c.execute(
                f"INSERT INTO sources({cols}) VALUES ({qs}) "
                "ON CONFLICT(platform, channel_id) DO UPDATE SET "
                + ",".join(f"{k}=excluded.{k}" for k in fields if k not in ("platform", "channel_id", "created_at")),
                tuple(fields.values()),
            )
            if cur.lastrowid:
                row = c.execute("SELECT id FROM sources WHERE platform=? AND channel_id=?",
                                (fields["platform"], fields["channel_id"])).fetchone()
                return row["id"]
            return cur.lastrowid

    def update_source(self, source_id: int, **fields) -> None:
        sets = ",".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE sources SET {sets} WHERE id=?", (*fields.values(), source_id))

    def source(self, source_id: int | None) -> dict | None:
        if source_id is None:
            return None
        row = self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def source_by_channel(self, platform: str, channel_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM sources WHERE platform=? AND channel_id=?",
                                (platform, channel_id)).fetchone()
        return dict(row) if row else None

    def sources(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM sources ORDER BY id")]

    def source_earnings(self, source_id: int) -> tuple[int, float]:
        """Total views of posted clips from a source, and the estimated program payout."""
        row = self.conn.execute(
            """SELECT COALESCE(SUM(p.views), 0) AS v FROM posts p JOIN clips c ON c.id = p.clip_id
               WHERE c.source_id=?""", (source_id,)).fetchone()
        src = self.source(source_id) or {}
        views = int(row["v"])
        return views, views / 1000.0 * float(src.get("payout_per_1k") or 0)

    # --- videos -----------------------------------------------------------
    def upsert_video(self, **fields) -> int:
        cols, qs = ",".join(fields), ",".join("?" for _ in fields)
        with self.tx() as c:
            c.execute(
                f"INSERT INTO videos({cols}) VALUES ({qs}) "
                "ON CONFLICT(platform, external_id) DO UPDATE SET views=excluded.views, title=excluded.title",
                tuple(fields.values()),
            )
            row = c.execute("SELECT id FROM videos WHERE platform=? AND external_id=?",
                            (fields["platform"], fields["external_id"])).fetchone()
            return row["id"]

    def update_video(self, video_id: int, **fields) -> None:
        sets = ",".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE videos SET {sets} WHERE id=?", (*fields.values(), video_id))

    def video(self, video_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        return dict(row) if row else None

    def videos(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM videos" + (" WHERE status=?" if status else "") + " ORDER BY views DESC"
        return [dict(r) for r in self.conn.execute(q, (status,) if status else ())]

    # --- clips ------------------------------------------------------------
    def add_clip(self, **fields) -> int:
        fields.setdefault("created_at", time.time())
        cols = ",".join(fields)
        qs = ",".join("?" for _ in fields)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO clips({cols}) VALUES ({qs})", tuple(fields.values()))
            return cur.lastrowid

    def update_clip(self, clip_id: int, **fields) -> None:
        sets = ",".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE clips SET {sets} WHERE id=?", (*fields.values(), clip_id))

    def clips(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM clips WHERE status=? ORDER BY ai_score DESC, signal_score DESC", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM clips ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def clip(self, clip_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        return dict(row) if row else None

    # --- posts ------------------------------------------------------------
    def add_post(self, clip_id: int, platform: str, external_id: str, status: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO posts(clip_id, platform, external_id, status, posted_at) VALUES (?,?,?,?,?)",
                (clip_id, platform, external_id, status, time.time()),
            )

    def update_post_metrics(self, platform: str, external_id: str, m: dict) -> None:
        with self.tx() as c:
            c.execute(
                """UPDATE posts SET views=?, likes=?, comments=?, shares=?, metrics_at=?
                   WHERE platform=? AND external_id=?""",
                (m.get("views"), m.get("likes"), m.get("comments"), m.get("shares"),
                 time.time(), platform, external_id),
            )

    def best_posts(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            """SELECT p.platform, p.views, p.likes, p.shares, c.title, c.hook, c.hashtags
               FROM posts p JOIN clips c ON c.id = p.clip_id
               WHERE p.views IS NOT NULL ORDER BY p.views DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

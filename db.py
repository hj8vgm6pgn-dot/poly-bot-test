import sqlite3, time
from pathlib import Path

DB = Path(__file__).with_name("bot.sqlite3")

def conn():
    c = sqlite3.connect(DB, timeout=20, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def _cols(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

def _ensure_col(c, table, name, decl):
    if name not in _cols(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            market_id TEXT NOT NULL,
            market_slug TEXT,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            stake REAL NOT NULL,
            shares REAL NOT NULL,
            probability REAL NOT NULL,
            edge REAL NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            winner TEXT,
            pnl REAL DEFAULT 0
        )""")
        extra = {
            "start_twap":"REAL","entry_twap":"REAL","entry_move_bps":"REAL",
            "entry_seconds_left":"INTEGER","entry_spread":"REAL","entry_liquidity":"REAL",
            "entry_feed_quality":"TEXT","resolved_ts":"INTEGER",
            "raw_model_probability":"REAL","market_implied_probability":"REAL",
            "blended_probability":"REAL","model_market_gap":"REAL","strategy_version":"TEXT",
            "lag_score":"REAL","book_imbalance":"REAL","contract_velocity":"REAL",
            "spot_mom_5":"REAL","spot_mom_10":"REAL","spot_mom_20":"REAL",
            "spot_acceleration":"REAL","source_disagreement_bps":"REAL"
        }
        for n,d in extra.items():
            _ensure_col(c,"trades",n,d)

        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_market
                     ON trades(market_id)""")

        c.execute("""CREATE TABLE IF NOT EXISTS price_obs(
            ts REAL NOT NULL, price REAL NOT NULL, source TEXT NOT NULL,
            source_count INTEGER DEFAULT 1,
            source_disagreement_bps REAL DEFAULT 0
        )""")
        _ensure_col(c,"price_obs","source_disagreement_bps","REAL DEFAULT 0")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_price_ts ON price_obs(ts)""")

        c.execute("""CREATE TABLE IF NOT EXISTS market_refs(
            market_slug TEXT PRIMARY KEY,
            start_ts INTEGER NOT NULL,
            start_twap REAL,
            capture_method TEXT,
            ref_age_seconds REAL,
            created_ts INTEGER NOT NULL
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS book_obs(
            ts REAL NOT NULL,
            market_slug TEXT NOT NULL,
            up_bid REAL, up_ask REAL,
            down_bid REAL, down_ask REAL,
            up_bid_depth REAL, up_ask_depth REAL,
            down_bid_depth REAL, down_ask_depth REAL
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_book_market_ts
                     ON book_obs(market_slug,ts)""")

        c.execute("""CREATE TABLE IF NOT EXISTS signal_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            market_id TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            seconds_left INTEGER NOT NULL,
            move_bps REAL NOT NULL,
            up_ask REAL NOT NULL,
            down_ask REAL NOT NULL,
            raw_up_probability REAL NOT NULL,
            blended_up_probability REAL NOT NULL,
            market_up_probability REAL,
            chosen_side TEXT,
            chosen_probability REAL,
            market_price REAL,
            edge REAL,
            lag_score REAL,
            book_imbalance REAL,
            contract_velocity REAL,
            spot_mom_5 REAL,
            spot_mom_10 REAL,
            spot_mom_20 REAL,
            spot_acceleration REAL,
            source_disagreement_bps REAL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            winner TEXT
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_signal_market
                     ON signal_log(market_slug)""")

        c.execute("""CREATE TABLE IF NOT EXISTS events(
            ts INTEGER NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
        )""")

def log(level, message):
    with conn() as c:
        c.execute("INSERT INTO events(ts,level,message) VALUES(?,?,?)",
                  (int(time.time()), level, message))
        c.execute("""DELETE FROM events WHERE rowid NOT IN
                     (SELECT rowid FROM events ORDER BY rowid DESC LIMIT 1000)""")

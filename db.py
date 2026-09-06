import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("bot.sqlite3")

def conn():
    c = sqlite3.connect(DB, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

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
        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_market
                     ON trades(market_id)""")
        c.execute("""CREATE TABLE IF NOT EXISTS price_obs(
            ts REAL NOT NULL,
            price REAL NOT NULL,
            source TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS events(
            ts INTEGER NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL
        )""")

def log(level, message):
    import time
    with conn() as c:
        c.execute("INSERT INTO events(ts,level,message) VALUES(?,?,?)",
                  (int(time.time()), level, message))
        c.execute("""DELETE FROM events WHERE rowid NOT IN
                     (SELECT rowid FROM events ORDER BY rowid DESC LIMIT 500)""")

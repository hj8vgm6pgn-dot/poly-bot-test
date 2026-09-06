import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("bot.sqlite3")

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            market_id TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            stake REAL NOT NULL,
            shares REAL NOT NULL,
            probability REAL NOT NULL,
            edge REAL NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            pnl REAL DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS twap(
            ts INTEGER PRIMARY KEY,
            price REAL NOT NULL
        )""")

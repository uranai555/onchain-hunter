"""SQLite storage for wallet discovery pipeline.

Schema:
  wallet_candidates — canonical wallet table (source-agnostic)
  discovery_events  — detected price/volatility events
  event_winners     — wallets that profited from specific events
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# ── Schema ──────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wallet_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address  TEXT NOT NULL UNIQUE,
    chain           TEXT NOT NULL DEFAULT 'hyperliquid',
    source_surface  TEXT NOT NULL,
    discovery_reason TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    source_confidence TEXT NOT NULL DEFAULT 'low'
                     CHECK(source_confidence IN ('low','medium','high')),
    raw_score       REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'candidate'
                     CHECK(status IN ('candidate','watchlist','active','rejected','archived')),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS discovery_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    event_time      TEXT NOT NULL,
    symbol          TEXT NOT NULL DEFAULT '',
    price_before    REAL,
    price_after     REAL,
    price_change_pct REAL,
    description     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(event_type, event_time, symbol)
);

CREATE TABLE IF NOT EXISTS event_winners (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL,
    wallet_address  TEXT NOT NULL,
    pre_positioning_score REAL DEFAULT 0,
    execution_score       REAL DEFAULT 0,
    exit_quality          REAL DEFAULT 0,
    estimated_pnl         REAL DEFAULT 0,
    trade_count_in_window  INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    FOREIGN KEY (event_id) REFERENCES discovery_events(id),
    UNIQUE(event_id, wallet_address)
);

CREATE INDEX IF NOT EXISTS idx_wallet_address ON wallet_candidates(wallet_address);
CREATE INDEX IF NOT EXISTS idx_event_time    ON discovery_events(event_time);
CREATE INDEX IF NOT EXISTS idx_ew_event      ON event_winners(event_id);
"""


# ── Connection ──────────────────────────────────────────────────────

def get_connection(db_path: str | Path = "data/onchain_wallets.sqlite") -> sqlite3.Connection:
    """Get a SQLite connection with row factory and WAL mode."""
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ── wallet_candidates CRUD ──────────────────────────────────────────

def upsert_candidate(
    conn: sqlite3.Connection,
    wallet_address: str,
    source_surface: str,
    chain: str = "hyperliquid",
    discovery_reason: str | None = None,
    source_confidence: str = "low",
    raw_score: float = 0.0,
    notes: str | None = None,
) -> bool:
    """Insert or update a wallet candidate. Returns True if new, False if updated."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        """SELECT id, first_seen_at FROM wallet_candidates
           WHERE wallet_address = ?""",
        (wallet_address,),
    )
    existing = cur.fetchone()
    if existing:
        conn.execute(
            """UPDATE wallet_candidates
               SET last_seen_at = ?,
                   source_surface = CASE WHEN ? != '' THEN ? ELSE source_surface END,
                   discovery_reason = CASE WHEN ? IS NOT NULL THEN ? ELSE discovery_reason END,
                   source_confidence = CASE WHEN ? != 'low' THEN ? ELSE source_confidence END,
                   raw_score = CASE WHEN ? > 0 THEN ? ELSE raw_score END,
                   notes = CASE WHEN ? IS NOT NULL THEN ? ELSE notes END
               WHERE id = ?""",
            (now,
             source_surface, source_surface,
             discovery_reason, discovery_reason,
             source_confidence, source_confidence,
             raw_score, raw_score,
             notes, notes,
             existing["id"]),
        )
        conn.commit()
        return False
    conn.execute(
        """INSERT INTO wallet_candidates
           (wallet_address, chain, source_surface, discovery_reason,
            first_seen_at, last_seen_at, source_confidence, raw_score, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (wallet_address, chain, source_surface, discovery_reason,
         now, now, source_confidence, raw_score, notes),
    )
    conn.commit()
    return True


def get_candidates(
    conn: sqlite3.Connection,
    status: str | None = None,
    min_confidence: str | None = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Load wallet candidates as a DataFrame."""
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if min_confidence:
        # Map confidence text to numeric for proper ordering: low=1, medium=2, high=3
        conf_values = {"low": 1, "medium": 2, "high": 3}
        conf_num = conf_values.get(min_confidence, 1)
        clauses.append(
            "CASE source_confidence WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 ELSE 0 END >= ?"
        )
        params.append(conf_num)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    df = pd.read_sql_query(
        f"SELECT * FROM wallet_candidates {where} ORDER BY raw_score DESC LIMIT ?",
        conn,
        params=params + [limit],
    )
    return df


def get_all_wallet_addresses(conn: sqlite3.Connection) -> list[str]:
    """Return all non-rejected wallet addresses."""
    rows = conn.execute(
        "SELECT wallet_address FROM wallet_candidates WHERE status != 'rejected'"
    ).fetchall()
    return [row["wallet_address"] for row in rows]


def set_candidate_status(
    conn: sqlite3.Connection,
    wallet_address: str,
    status: str,
) -> None:
    """Update the status of a wallet candidate."""
    conn.execute(
        "UPDATE wallet_candidates SET status = ? WHERE wallet_address = ?",
        (status, wallet_address),
    )
    conn.commit()


# ── discovery_events CRUD ───────────────────────────────────────────

def record_event(
    conn: sqlite3.Connection,
    event_type: str,
    event_time: str,
    symbol: str = "",
    price_before: float | None = None,
    price_after: float | None = None,
    price_change_pct: float | None = None,
    description: str | None = None,
) -> int:
    """Insert or update a discovery event. Returns the (stable) event id.

    Deduplicates on (event_type, event_time, symbol) so repeated daily runs over
    the same lookback window reuse the existing row instead of creating a new one.
    """
    existing = conn.execute(
        """SELECT id FROM discovery_events
           WHERE event_type = ? AND event_time = ? AND symbol = ?""",
        (event_type, event_time, symbol),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE discovery_events
               SET price_before = ?, price_after = ?,
                   price_change_pct = ?, description = ?
               WHERE id = ?""",
            (price_before, price_after, price_change_pct, description,
             existing["id"]),
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO discovery_events
           (event_type, event_time, symbol, price_before, price_after,
            price_change_pct, description)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (event_type, event_time, symbol, price_before, price_after,
         price_change_pct, description),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_recent_events(
    conn: sqlite3.Connection,
    hours: int = 48,
    limit: int = 20,
) -> pd.DataFrame:
    """Load recent discovery events."""
    df = pd.read_sql_query(
        """SELECT * FROM discovery_events
           WHERE event_time >= datetime('now', ?)
           ORDER BY event_time DESC LIMIT ?""",
        conn,
        params=(f"-{hours} hours", limit),
    )
    return df


# ── event_winners CRUD ──────────────────────────────────────────────

def record_winner(
    conn: sqlite3.Connection,
    event_id: int,
    wallet_address: str,
    pre_positioning_score: float = 0.0,
    execution_score: float = 0.0,
    exit_quality: float = 0.0,
    estimated_pnl: float = 0.0,
    trade_count_in_window: int = 0,
) -> bool:
    """Record a wallet's event performance. Returns True if new, False if updated."""
    cur = conn.execute(
        "SELECT id FROM event_winners WHERE event_id = ? AND wallet_address = ?",
        (event_id, wallet_address),
    )
    if cur.fetchone():
        conn.execute(
            """UPDATE event_winners
               SET pre_positioning_score = ?,
                   execution_score = ?,
                   exit_quality = ?,
                   estimated_pnl = ?,
                   trade_count_in_window = ?
               WHERE event_id = ? AND wallet_address = ?""",
            (pre_positioning_score, execution_score, exit_quality,
             estimated_pnl, trade_count_in_window,
             event_id, wallet_address),
        )
        conn.commit()
        return False
    conn.execute(
        """INSERT INTO event_winners
           (event_id, wallet_address, pre_positioning_score, execution_score,
            exit_quality, estimated_pnl, trade_count_in_window)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (event_id, wallet_address, pre_positioning_score, execution_score,
         exit_quality, estimated_pnl, trade_count_in_window),
    )
    conn.commit()
    return True


# ── Migration: import existing CSV ──────────────────────────────────

def import_from_csv(conn: sqlite3.Connection, csv_path: str | Path) -> dict[str, int]:
    """Import existing candidate_hyperliquid_wallets.csv into SQLite.

    Returns counts: {imported, skipped, total}.
    """
    path = Path(csv_path)
    if not path.exists():
        return {"imported": 0, "skipped": 0, "total": 0}
    df = pd.read_csv(path)
    if df.empty:
        return {"imported": 0, "skipped": 0, "total": 0}

    total = len(df)
    imported = 0
    skipped = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for _, row in df.iterrows():
        address = str(row.get("wallet_address", "")).strip().lower()
        if not address:
            skipped += 1
            continue
        source = str(row.get("source", "legacy_csv")).strip()
        notes = str(row.get("notes", "")).strip()
        rank = str(row.get("rank", ""))
        name = str(row.get("name", ""))
        discovery_reason = f"Legacy import: source={source}, rank={rank}"
        if name and name != "nan":
            discovery_reason += f", name={name}"

        cur = conn.execute(
            "SELECT id FROM wallet_candidates WHERE wallet_address = ?",
            (address,),
        )
        if cur.fetchone():
            skipped += 1
            continue

        conn.execute(
            """INSERT INTO wallet_candidates
               (wallet_address, chain, source_surface, discovery_reason,
                first_seen_at, last_seen_at, source_confidence, notes)
               VALUES (?, 'hyperliquid', ?, ?, ?, ?, 'medium', ?)""",
            (address, "legacy_csv", discovery_reason, now, now, notes),
        )
        imported += 1

    conn.commit()
    return {"imported": imported, "skipped": skipped, "total": total}
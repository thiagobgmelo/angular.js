"""Persistência SQLite de sinais, posições e curva de equity.

Thread-safe: uma única conexão protegida por lock (o FastAPI executa
endpoints sync em threadpool, então leituras/escritas podem concorrer).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

# colunas que update_position pode alterar — nunca montar SQL com chave livre
ALLOWED_UPDATE_COLUMNS = frozenset(
    {"stop", "remaining_size", "status", "closed_at", "realized_pnl", "events", "targets"}
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    targets TEXT NOT NULL,
    score INTEGER NOT NULL,
    max_score INTEGER NOT NULL,
    position_size REAL NOT NULL,
    risk_amount REAL NOT NULL,
    rationale TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    targets TEXT NOT NULL,
    size REAL NOT NULL,
    remaining_size REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open | closed
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL NOT NULL DEFAULT 0,
    events TEXT NOT NULL DEFAULT '[]'      -- histórico: tp1, breakeven, stop...
);
CREATE TABLE IF NOT EXISTS equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|expired
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decided_via TEXT                          -- telegram|dashboard|timeout
);
CREATE TABLE IF NOT EXISTS execution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    action TEXT NOT NULL,                     -- entry|tp_orders|stop_order|amend_stop|error|skip
    symbol TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    ok INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS radar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    score INTEGER NOT NULL,
    max_score INTEGER NOT NULL,
    missing TEXT NOT NULL,          -- o que falta para virar sinal
    price REAL NOT NULL,
    context TEXT NOT NULL DEFAULT '{}',
    promoted_signal_id INTEGER REFERENCES signals(id)
);
"""

# migrações leves: colunas adicionadas após a v1 (ALTER se ausente)
MIGRATIONS = [
    ("signals", "context", "TEXT NOT NULL DEFAULT '{}'"),
    ("signals", "suggested_leverage", "INTEGER NOT NULL DEFAULT 1"),
    ("signals", "margin_required", "REAL NOT NULL DEFAULT 0"),
    ("signals", "liquidation_price_est", "REAL"),
    ("signals", "leverage_rationale", "TEXT NOT NULL DEFAULT ''"),
]


class Store:
    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            for table, column, decl in MIGRATIONS:
                cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            self.conn.commit()

    # --- sinais ---
    def save_signal(self, signal_dict: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO signals (created_at, symbol, timeframe, direction, trade_type,
                     entry, stop, targets, score, max_score, position_size, risk_amount,
                     rationale, context, suggested_leverage, margin_required,
                     liquidation_price_est, leverage_rationale)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_dict["created_at"], signal_dict["symbol"], signal_dict["timeframe"],
                    signal_dict["direction"], signal_dict["trade_type"], signal_dict["entry"],
                    signal_dict["stop"], json.dumps(signal_dict["targets"]), signal_dict["score"],
                    signal_dict["max_score"], signal_dict["position_size"],
                    signal_dict["risk_amount"], json.dumps(signal_dict["rationale"]),
                    json.dumps(signal_dict.get("context", {})),
                    signal_dict.get("suggested_leverage", 1),
                    signal_dict.get("margin_required", 0.0),
                    signal_dict.get("liquidation_price_est"),
                    signal_dict.get("leverage_rationale", ""),
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def recent_signals(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._decode_signal(r) for r in rows]

    def has_recent_signal(
        self, symbol: str, timeframe: str, direction: str, since_iso: str
    ) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM signals
                   WHERE symbol=? AND timeframe=? AND direction=? AND created_at >= ? LIMIT 1""",
                (symbol, timeframe, direction, since_iso),
            ).fetchone()
        return row is not None

    @staticmethod
    def _decode_signal(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["targets"] = json.loads(d["targets"])
        d["rationale"] = json.loads(d["rationale"])
        d["context"] = json.loads(d.get("context") or "{}")
        return d

    # --- radar (oportunidades em formação) ---
    def save_radar_event(self, ev: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO radar_events (created_at, symbol, timeframe, direction,
                     score, max_score, missing, price, context)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    ev["created_at"], ev["symbol"], ev["timeframe"], ev["direction"],
                    ev["score"], ev["max_score"], ev["missing"], ev["price"],
                    json.dumps(ev.get("context", {})),
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def recent_radar(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM radar_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["context"] = json.loads(d.get("context") or "{}")
            out.append(d)
        return out

    def has_recent_radar(
        self, symbol: str, timeframe: str, direction: str, since_iso: str
    ) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM radar_events
                   WHERE symbol=? AND timeframe=? AND direction=? AND created_at >= ?
                   LIMIT 1""",
                (symbol, timeframe, direction, since_iso),
            ).fetchone()
        return row is not None

    def promote_radar_events(
        self, symbol: str, timeframe: str, direction: str, since_iso: str, signal_id: int
    ) -> int:
        """Marca eventos de radar recentes como confirmados pelo sinal."""
        with self._lock:
            cur = self.conn.execute(
                """UPDATE radar_events SET promoted_signal_id=?
                   WHERE symbol=? AND timeframe=? AND direction=? AND created_at >= ?
                     AND promoted_signal_id IS NULL""",
                (signal_id, symbol, timeframe, direction, since_iso),
            )
            self.conn.commit()
            return cur.rowcount

    # --- posições ---
    def open_position(self, signal_id: int, signal_dict: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO positions (signal_id, symbol, timeframe, direction, entry, stop,
                     targets, size, remaining_size, status, opened_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'open',?)""",
                (
                    signal_id, signal_dict["symbol"], signal_dict["timeframe"],
                    signal_dict["direction"], signal_dict["entry"], signal_dict["stop"],
                    json.dumps(signal_dict["targets"]), signal_dict["position_size"],
                    signal_dict["position_size"], signal_dict["created_at"],
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def positions(self, status: str | None = None) -> list[dict]:
        with self._lock:
            if status:
                rows = self.conn.execute(
                    "SELECT * FROM positions WHERE status=? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM positions ORDER BY id DESC"
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["targets"] = json.loads(d["targets"])
            d["events"] = json.loads(d["events"])
            out.append(d)
        return out

    def update_position(self, pos_id: int, **fields) -> None:
        unknown = set(fields) - ALLOWED_UPDATE_COLUMNS
        if unknown:
            raise ValueError(f"Colunas não permitidas em update_position: {sorted(unknown)}")
        for key in ("targets", "events"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self.conn.execute(
                f"UPDATE positions SET {sets} WHERE id=?",  # noqa: S608 — colunas whitelisted
                (*fields.values(), pos_id),
            )
            self.conn.commit()

    # --- settings (estado runtime persistido: modo de execução, pausa) ---
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self.conn.commit()

    # --- aprovações (modo manual de execução) ---
    def create_approval(self, signal_id: int, created_at: str, expires_at: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO approvals (signal_id, created_at, expires_at) VALUES (?,?,?)",
                (signal_id, created_at, expires_at),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_approval(self, approval_id: int) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                """SELECT a.*, s.symbol, s.timeframe, s.direction, s.entry, s.stop,
                          s.targets, s.position_size, s.suggested_leverage
                   FROM approvals a JOIN signals s ON s.id = a.signal_id
                   WHERE a.id=?""",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["targets"] = json.loads(d["targets"])
        return d

    def pending_approvals(self, now_iso: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT a.*, s.symbol, s.timeframe, s.direction, s.entry, s.stop,
                          s.targets, s.position_size, s.suggested_leverage
                   FROM approvals a JOIN signals s ON s.id = a.signal_id
                   WHERE a.status='pending' AND a.expires_at > ?
                   ORDER BY a.id DESC""",
                (now_iso,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["targets"] = json.loads(d["targets"])
            out.append(d)
        return out

    def decide_approval(
        self, approval_id: int, status: str, decided_at: str, via: str
    ) -> bool:
        """Decide atomicamente; False se já não estava mais pendente."""
        with self._lock:
            cur = self.conn.execute(
                """UPDATE approvals SET status=?, decided_at=?, decided_via=?
                   WHERE id=? AND status='pending'""",
                (status, decided_at, via, approval_id),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def expire_stale_approvals(self, now_iso: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                """UPDATE approvals SET status='expired', decided_via='timeout'
                   WHERE status='pending' AND expires_at <= ?""",
                (now_iso,),
            )
            self.conn.commit()
            return cur.rowcount

    # --- log de execução (auditoria de toda ordem, real ou dry-run) ---
    def log_execution(self, at: str, action: str, symbol: str, detail: dict, ok: bool) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO execution_log (at, action, symbol, detail, ok) VALUES (?,?,?,?,?)",
                (at, action, symbol, json.dumps(detail), int(ok)),
            )
            self.conn.commit()

    def execution_log_recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM execution_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"])
            d["ok"] = bool(d["ok"])
            out.append(d)
        return out

    def entries_since(self, since_iso: str) -> int:
        """Quantas entradas reais/dry-run desde o instante dado (circuit breaker)."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) c FROM execution_log WHERE action='entry' AND ok=1 AND at >= ?",
                (since_iso,),
            ).fetchone()
        return int(row["c"])

    def realized_pnl_since(self, since_iso: str) -> float:
        """PnL realizado das posições fechadas desde o instante (circuit breaker)."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) s FROM positions "
                "WHERE status='closed' AND closed_at >= ?",
                (since_iso,),
            ).fetchone()
        return float(row["s"])

    # --- equity ---
    def record_equity(self, at_iso: str, value: float) -> None:
        with self._lock:
            self.conn.execute("INSERT INTO equity (at, value) VALUES (?,?)", (at_iso, value))
            self.conn.commit()

    def equity_curve(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT at, value FROM equity ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def realized_pnl_total(self) -> float:
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) s FROM positions"
            ).fetchone()
        return float(row["s"])

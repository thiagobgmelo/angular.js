from datetime import UTC, datetime, timedelta

import pytest

from app.paper.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "radar.db")


def radar_ev(direction="long", created_at=None, symbol="BTC/USDT", tf="1h"):
    return {
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "timeframe": tf,
        "direction": direction,
        "score": 3,
        "max_score": 7,
        "missing": "falta(m) 1 ponto(s) de confluência",
        "price": 100.0,
        "context": {"rsi": 41.2},
    }


def test_save_and_list_radar(store):
    store.save_radar_event(radar_ev())
    rows = store.recent_radar()
    assert len(rows) == 1
    assert rows[0]["missing"].startswith("falta")
    assert rows[0]["context"]["rsi"] == 41.2
    assert rows[0]["promoted_signal_id"] is None


def test_radar_dedup_window(store):
    now = datetime.now(UTC)
    store.save_radar_event(radar_ev(created_at=now.isoformat()))
    since = (now - timedelta(hours=1)).isoformat()
    assert store.has_recent_radar("BTC/USDT", "1h", "long", since) is True
    assert store.has_recent_radar("BTC/USDT", "1h", "short", since) is False
    assert store.has_recent_radar("ETH/USDT", "1h", "long", since) is False


def test_promotion_marks_matching_events(store):
    now = datetime.now(UTC)
    store.save_radar_event(radar_ev(created_at=(now - timedelta(hours=2)).isoformat()))
    store.save_radar_event(
        radar_ev(direction="short", created_at=(now - timedelta(hours=2)).isoformat())
    )
    since = (now - timedelta(hours=24)).isoformat()
    promoted = store.promote_radar_events("BTC/USDT", "1h", "long", since, signal_id=42)
    assert promoted == 1
    rows = {r["direction"]: r for r in store.recent_radar()}
    assert rows["long"]["promoted_signal_id"] == 42
    assert rows["short"]["promoted_signal_id"] is None


def test_migration_adds_context_to_old_signals_table(tmp_path):
    """Banco criado por versão antiga (sem colunas novas) migra sem erro."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, symbol TEXT,
            timeframe TEXT, direction TEXT, trade_type TEXT, entry REAL, stop REAL,
            targets TEXT, score INTEGER, max_score INTEGER, position_size REAL,
            risk_amount REAL, rationale TEXT)"""
    )
    conn.commit()
    conn.close()
    store = Store(db)  # deve aplicar as migrações
    cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(signals)")}
    assert {"context", "suggested_leverage", "leverage_rationale"} <= cols

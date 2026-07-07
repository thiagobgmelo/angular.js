import asyncio

import pytest

from app.config import Config
from app.data.feed import DemoFeed
from app.engine import TradingEngine


def make_cfg(tmp_path, threshold=4):
    return Config({
        "market": {"pairs": ["BTC/USDT"], "timeframes": ["1h"], "candles": 400},
        "strategy": {"score_threshold": threshold, "min_risk_reward": 1.5,
                     "swing_lookback": 5, "sr_zone_tolerance": 0.005},
        "risk": {"account_equity": 10000, "risk_per_trade": 0.01, "max_open_positions": 5},
        "paper": {"db_path": str(tmp_path / "engine_test.db")},
        "feed": {"tick_throttle_ms": 0, "stale_after_s": 90},
    })


@pytest.fixture
def engine_and_feed(tmp_path, monkeypatch):
    # db_path do engine é relativo ao PROJECT_ROOT; usa caminho absoluto do tmp
    cfg = make_cfg(tmp_path)
    feed = DemoFeed(["BTC/USDT"], ["1h"], tick_throttle_ms=0)
    engine = TradingEngine(cfg, feed)
    return engine, feed


def seed_feed(feed):
    df = feed.client.fetch_ohlcv("BTC/USDT", "1h", limit=450)
    rows = [
        [int(ts.timestamp() * 1000), r["open"], r["high"], r["low"], r["close"], r["volume"]]
        for ts, r in df.iterrows()
    ]
    feed.caches[("BTC/USDT", "1h")].seed(rows)


def test_candle_close_triggers_analysis_and_bus(engine_and_feed):
    engine, feed = engine_and_feed
    seed_feed(feed)

    async def scenario():
        q = engine.subscribe()
        last = feed.caches[("BTC/USDT", "1h")].rows[-1]
        new_ts = last[0] + 3_600_000
        await feed.ingest_candle("BTC/USDT", "1h", [new_ts, last[4], last[4], last[4], last[4], 1])
        # espera o callback + análise em thread concluírem
        for _ in range(200):
            await asyncio.sleep(0.01)
            if engine.stats["candles_closed"] and engine.stats["last_analysis_ms"] is not None:
                break
        return q

    q = asyncio.run(scenario())
    assert engine.stats["candles_closed"] == 1
    assert engine.stats["last_analysis_ms"] is not None  # análise rodou e foi medida
    ev = q.get_nowait()
    assert ev["type"] == "candle"
    assert ev["candle"]["time"] > 0


def test_tick_updates_open_position(engine_and_feed):
    engine, feed = engine_and_feed
    sig = {
        "created_at": "2024-01-01T00:00:00+00:00", "symbol": "BTC/USDT",
        "timeframe": "1h", "direction": "long", "trade_type": "day_trade",
        "entry": 100.0, "stop": 95.0, "targets": [107.5, 112.5, 120.0],
        "score": 5, "max_score": 7, "position_size": 20.0,
        "risk_amount": 100.0, "rationale": ["teste"],
    }
    sig_id = engine.store.save_signal(sig)
    engine.portfolio.open_from_signal(sig_id, sig)

    async def scenario():
        await engine.handle_tick("BTC/USDT", 94.0, 1_700_000_000_000)

    asyncio.run(scenario())
    closed = engine.portfolio.closed_positions()
    assert len(closed) == 1
    assert closed[0]["events"][-1]["type"] == "stop"
    # fill no nível exato do stop: perda = risco configurado
    assert abs(closed[0]["realized_pnl"] + 100.0) < 1e-9


def test_scan_all_returns_list(engine_and_feed):
    engine, feed = engine_and_feed
    seed_feed(feed)
    result = asyncio.run(engine.scan_all())
    assert isinstance(result, list)
    health = engine.health()
    assert "feed" in health and "engine" in health

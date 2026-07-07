import asyncio

import pytest

from app.data.feed import BaseFeed, CandleCache, next_close_ts, timeframe_seconds


def test_timeframe_seconds():
    assert timeframe_seconds("15m") == 900
    assert timeframe_seconds("1h") == 3600
    assert timeframe_seconds("4h") == 14400
    assert timeframe_seconds("1d") == 86400
    with pytest.raises(ValueError):
        timeframe_seconds("7x")


def test_next_close_alignment():
    # 10:07:33 em timeframe 15m → próximo fechamento 10:15:00
    now = 10 * 3600 + 7 * 60 + 33
    assert next_close_ts(now, 900) == 10 * 3600 + 15 * 60
    # exatamente no fechamento → próximo período
    assert next_close_ts(3600, 3600) == 7200


def test_candle_cache_close_detection():
    cache = CandleCache()
    cache.seed([[1000, 1, 2, 0.5, 1.5, 10], [2000, 1.5, 2.5, 1.0, 2.0, 12]])
    # atualização do candle em formação: sem fechamento
    assert cache.ingest([2000, 1.5, 2.6, 1.0, 2.1, 15]) is None
    assert cache.rows[-1][4] == 2.1
    # candle novo → o anterior fechou com o último estado conhecido
    closed = cache.ingest([3000, 2.1, 2.2, 2.0, 2.15, 1])
    assert closed is not None
    assert closed[0] == 2000 and closed[4] == 2.1
    # candle antigo fora de ordem: ignorado
    assert cache.ingest([1000, 9, 9, 9, 9, 9]) is None
    assert len(cache.rows) == 3


def test_candle_cache_df():
    cache = CandleCache()
    cache.seed([[1000, 1, 2, 0.5, 1.5, 10], [2000, 1.5, 2.5, 1.0, 2.0, 12]])
    df = cache.df()
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df["close"].iloc[-1] == 2.0


def test_base_feed_events_and_throttle():
    feed = BaseFeed(["BTC/USDT"], ["1h"], tick_throttle_ms=10_000)
    candle_events, tick_events = [], []

    async def on_candle(symbol, tf, closed):
        candle_events.append((symbol, tf, closed))

    async def on_tick(symbol, price, ts):
        tick_events.append((symbol, price))

    feed.on_candle_close(on_candle)
    feed.on_tick(on_tick)

    async def scenario():
        feed.caches[("BTC/USDT", "1h")].seed([[1000, 1, 2, 0.5, 1.5, 10]])
        await feed.ingest_candle("BTC/USDT", "1h", [2000, 1.5, 2, 1, 1.8, 5])
        await asyncio.sleep(0)  # deixa a task do callback rodar
        # dois ticks dentro da janela de throttle: só o primeiro emite
        await feed.ingest_tick("BTC/USDT", 100.0, 1_700_000_000_000)
        await feed.ingest_tick("BTC/USDT", 101.0, 1_700_000_000_500)

    asyncio.run(scenario())
    assert len(candle_events) == 1
    assert candle_events[0][2][0] == 1000  # candle fechado é o anterior
    assert len(tick_events) == 1
    # o último preço é sempre atualizado, mesmo com throttle
    assert feed.last_price("BTC/USDT") == 101.0


def test_market_feed_restart_respawns_all_streams():
    """O watchdog reinicia os streams: _restart deve recriar 1 task por
    par×timeframe de candles + 1 de ticker por par."""
    from app.data.feed import MarketFeed

    feed = MarketFeed("binance", ["BTC/USDT", "ETH/USDT"], ["1h", "4h"])
    spawned = []

    def fake_spawn(coro):
        spawned.append(coro.cr_code.co_name)
        coro.close()

    feed._spawn = fake_spawn

    async def scenario():
        await feed._restart()
        await feed.exchange.close()

    asyncio.run(scenario())
    assert spawned.count("_watch_ohlcv_loop") == 4  # 2 pares × 2 timeframes
    assert spawned.count("_watch_ticker_loop") == 2


def test_health_reports_streams():
    feed = BaseFeed(["BTC/USDT"], ["1h"], stale_after_s=90)
    feed.caches[("BTC/USDT", "1h")].seed([[1000, 1, 2, 0.5, 1.5, 10]])
    health = feed.health()
    stream = health["streams"]["BTC/USDT 1h"]
    assert stream["candles"] == 1
    assert stream["data_age_s"] is not None
    assert stream["stale"] is False

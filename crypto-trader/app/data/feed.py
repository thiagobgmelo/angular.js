"""Feed de mercado em tempo real com cache em memória.

Três implementações sobre a mesma base:
- MarketFeed (websocket ccxt.pro): watch_ohlcv + watch_ticker, latência sub-segundo
- MarketFeed em modo `rest`: fetch alinhado ao relógio dos candles (fallback)
- DemoFeed: ticks sintéticos sobre as séries do DemoClient (offline/testes)

O feed é a única fonte de dados do modo ao vivo: análise, posições e API
leem do cache (sem I/O), e reagem a eventos de candle fechado e de tick.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

import numpy as np
import pandas as pd

from .exchange import DemoClient

log = logging.getLogger(__name__)

TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "8h": 28800, "12h": 43200, "1d": 86400,
}

CandleCallback = Callable[[str, str, list], Awaitable[None]]
TickCallback = Callable[[str, float, int], Awaitable[None]]


def timeframe_seconds(tf: str) -> int:
    if tf not in TF_SECONDS:
        raise ValueError(f"Timeframe desconhecido: {tf!r}")
    return TF_SECONDS[tf]


def next_close_ts(now_s: float, tf_s: int) -> int:
    """Timestamp (s) do próximo fechamento teórico de candle."""
    return (int(now_s) // tf_s + 1) * tf_s


class CandleCache:
    """Janela rolante de candles [ts_ms, o, h, l, c, v]; último = em formação."""

    def __init__(self, maxlen: int = 600):
        self.rows: deque[list] = deque(maxlen=maxlen)
        self.last_event: float = 0.0

    def seed(self, rows: list[list]) -> None:
        for row in rows:
            if not self.rows or row[0] > self.rows[-1][0]:
                self.rows.append(list(row))
        self.last_event = time.time()

    def ingest(self, row: list) -> list | None:
        """Aplica um candle novo/atualizado. Retorna o candle FECHADO, se houve."""
        self.last_event = time.time()
        if not self.rows or row[0] > self.rows[-1][0]:
            closed = list(self.rows[-1]) if self.rows else None
            self.rows.append(list(row))
            return closed
        if row[0] == self.rows[-1][0]:
            self.rows[-1] = list(row)
        return None  # candle antigo fora de ordem: ignorado

    def df(self) -> pd.DataFrame:
        rows = list(self.rows)
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.set_index("ts").astype(float)


class BaseFeed:
    def __init__(
        self,
        pairs: list[str],
        timeframes: list[str],
        tick_throttle_ms: int = 1000,
        stale_after_s: int = 90,
    ):
        self.pairs = pairs
        self.timeframes = timeframes
        self.tick_throttle_s = tick_throttle_ms / 1000.0
        self.stale_after_s = stale_after_s
        self.caches: dict[tuple[str, str], CandleCache] = {
            (p, tf): CandleCache() for p in pairs for tf in timeframes
        }
        self.last_prices: dict[str, tuple[float, int]] = {}
        self._last_tick_emit: dict[str, float] = {}
        self._candle_cbs: list[CandleCallback] = []
        self._tick_cbs: list[TickCallback] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self.started_at: float | None = None

    # --- assinatura de eventos ---
    def on_candle_close(self, cb: CandleCallback) -> None:
        self._candle_cbs.append(cb)

    def on_tick(self, cb: TickCallback) -> None:
        self._tick_cbs.append(cb)

    # --- leitura ---
    def df(self, symbol: str, timeframe: str) -> pd.DataFrame:
        cache = self.caches.get((symbol, timeframe))
        if cache is None:
            raise KeyError(f"Sem stream para {symbol} {timeframe}")
        return cache.df()

    def last_price(self, symbol: str) -> float | None:
        entry = self.last_prices.get(symbol)
        return entry[0] if entry else None

    def health(self) -> dict:
        now = time.time()
        streams = {}
        for (symbol, tf), cache in self.caches.items():
            age = now - cache.last_event if cache.last_event else None
            streams[f"{symbol} {tf}"] = {
                "candles": len(cache.rows),
                "data_age_s": round(age, 2) if age is not None else None,
                "stale": bool(age is not None and age > self.stale_after_s),
            }
        return {
            "mode": type(self).__name__,
            "uptime_s": round(now - self.started_at, 1) if self.started_at else None,
            "streams": streams,
            "last_prices": {
                s: {"price": p, "age_s": round(now - ts_ms / 1000.0, 2)}
                for s, (p, ts_ms) in self.last_prices.items()
            },
        }

    # --- ingestão (usada pelas subclasses e pelos testes) ---
    async def ingest_candle(self, symbol: str, timeframe: str, row: list) -> None:
        closed = self.caches[(symbol, timeframe)].ingest(row)
        if closed is not None:
            for cb in self._candle_cbs:
                # candle fechado é o gatilho da estratégia: não pode ser perdido,
                # mas também não deve travar o loop do websocket
                asyncio.get_running_loop().create_task(cb(symbol, timeframe, closed))

    async def ingest_tick(self, symbol: str, price: float, ts_ms: int) -> None:
        self.last_prices[symbol] = (float(price), int(ts_ms))
        now = time.monotonic()
        if now - self._last_tick_emit.get(symbol, 0.0) < self.tick_throttle_s:
            return
        self._last_tick_emit[symbol] = now
        for cb in self._tick_cbs:
            await cb(symbol, float(price), int(ts_ms))

    # --- ciclo de vida ---
    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    def _spawn(self, coro) -> None:
        self._tasks.append(asyncio.get_running_loop().create_task(coro))


class MarketFeed(BaseFeed):
    """Feed de exchange real: websocket (padrão) ou REST alinhado ao relógio."""

    def __init__(
        self,
        exchange_id: str,
        pairs: list[str],
        timeframes: list[str],
        mode: str = "websocket",
        tick_throttle_ms: int = 1000,
        stale_after_s: int = 90,
        seed_candles: int = 500,
    ):
        super().__init__(pairs, timeframes, tick_throttle_ms, stale_after_s)
        import ccxt.pro  # noqa: PLC0415 — import pesado, só no modo ao vivo

        self.exchange = getattr(ccxt.pro, exchange_id)({"enableRateLimit": True})
        self.mode = mode
        self.seed_candles = seed_candles

    async def start(self) -> None:
        self._running = True
        self.started_at = time.time()
        await self._seed_history()
        if self.mode == "websocket":
            for symbol in self.pairs:
                for tf in self.timeframes:
                    self._spawn(self._watch_ohlcv_loop(symbol, tf))
                self._spawn(self._watch_ticker_loop(symbol))
        else:
            for symbol in self.pairs:
                for tf in self.timeframes:
                    self._spawn(self._rest_candle_loop(symbol, tf))
                self._spawn(self._rest_ticker_loop(symbol))
        self._spawn(self._watchdog_loop())
        log.info("MarketFeed iniciado (%s, modo %s)", self.exchange.id, self.mode)

    async def stop(self) -> None:
        await super().stop()
        await self.exchange.close()

    async def _seed_history(self) -> None:
        for symbol in self.pairs:
            for tf in self.timeframes:
                raw = await self.exchange.fetch_ohlcv(symbol, tf, limit=self.seed_candles)
                self.caches[(symbol, tf)].seed(raw)
                log.info("seed %s %s: %d candles", symbol, tf, len(raw))

    async def _watch_ohlcv_loop(self, symbol: str, tf: str) -> None:
        backoff = 1.0
        while self._running:
            try:
                candles = await self.exchange.watch_ohlcv(symbol, tf)
                backoff = 1.0
                for row in candles:
                    await self.ingest_candle(symbol, tf, row)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.warning("watch_ohlcv %s %s: %s — reconectando", symbol, tf, err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _watch_ticker_loop(self, symbol: str) -> None:
        backoff = 1.0
        while self._running:
            try:
                t = await self.exchange.watch_ticker(symbol)
                backoff = 1.0
                if t.get("last") is not None:
                    ts = t.get("timestamp") or int(time.time() * 1000)
                    await self.ingest_tick(symbol, t["last"], ts)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.warning("watch_ticker %s: %s — reconectando", symbol, err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _rest_candle_loop(self, symbol: str, tf: str) -> None:
        """Fallback sem websocket: busca 2 s após cada fechamento teórico."""
        tf_s = timeframe_seconds(tf)
        while self._running:
            wait = next_close_ts(time.time(), tf_s) + 2 - time.time()
            await asyncio.sleep(max(wait, 0.5))
            try:
                raw = await self.exchange.fetch_ohlcv(symbol, tf, limit=3)
                for row in raw:
                    await self.ingest_candle(symbol, tf, row)
            except Exception as err:
                log.warning("rest fetch %s %s: %s", symbol, tf, err)

    async def _rest_ticker_loop(self, symbol: str) -> None:
        while self._running:
            try:
                t = await self.exchange.fetch_ticker(symbol)
                if t.get("last") is not None:
                    ts = t.get("timestamp") or int(time.time() * 1000)
                    await self.ingest_tick(symbol, t["last"], ts)
            except Exception as err:
                log.warning("rest ticker %s: %s", symbol, err)
            await asyncio.sleep(self.tick_throttle_s)

    async def _watchdog_loop(self) -> None:
        """Reinicia todos os streams se algum ficar sem eventos além do limite."""
        while self._running:
            await asyncio.sleep(self.stale_after_s / 3)
            now = time.time()
            stale = [
                key for key, cache in self.caches.items()
                if cache.last_event and now - cache.last_event > self.stale_after_s
                # timeframes longos naturalmente ficam sem update quando não há
                # trade; só considera stale se o próprio ticker também parou
                and self._ticker_stale(key[0], now)
            ]
            if stale:
                log.warning("streams sem dados %s — reiniciando feed", stale)
                await self._restart()

    def _ticker_stale(self, symbol: str, now: float) -> bool:
        entry = self.last_prices.get(symbol)
        return entry is None or now - entry[1] / 1000.0 > self.stale_after_s

    async def _restart(self) -> None:
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        self._tasks = [t for t in self._tasks if t is asyncio.current_task()]
        if self.mode == "websocket":
            for symbol in self.pairs:
                for tf in self.timeframes:
                    self._spawn(self._watch_ohlcv_loop(symbol, tf))
                self._spawn(self._watch_ticker_loop(symbol))
        else:
            for symbol in self.pairs:
                for tf in self.timeframes:
                    self._spawn(self._rest_candle_loop(symbol, tf))
                self._spawn(self._rest_ticker_loop(symbol))


class DemoFeed(BaseFeed):
    """Ticks sintéticos sobre as séries do DemoClient; candles fecham no relógio real."""

    def __init__(
        self,
        pairs: list[str],
        timeframes: list[str],
        tick_throttle_ms: int = 1000,
        stale_after_s: int = 90,
        tick_interval_s: float = 0.5,
    ):
        super().__init__(pairs, timeframes, tick_throttle_ms, stale_after_s)
        self.client = DemoClient()
        self.tick_interval_s = tick_interval_s

    async def start(self) -> None:
        self._running = True
        self.started_at = time.time()
        for symbol in self.pairs:
            for tf in self.timeframes:
                df = self.client.fetch_ohlcv(symbol, tf, limit=500)
                rows = [
                    [int(ts.timestamp() * 1000), r["open"], r["high"], r["low"],
                     r["close"], r["volume"]]
                    for ts, r in df.iterrows()
                ]
                self.caches[(symbol, tf)].seed(rows)
            self._spawn(self._tick_loop(symbol))
        log.info("DemoFeed iniciado (%d pares, tick %.1fs)", len(self.pairs), self.tick_interval_s)

    async def _tick_loop(self, symbol: str) -> None:
        rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
        price = None
        for tf in self.timeframes:  # começa do último close conhecido
            rows = self.caches[(symbol, tf)].rows
            if rows:
                price = rows[-1][4]
                break
        price = price or 100.0
        while self._running:
            await asyncio.sleep(self.tick_interval_s)
            price *= 1 + rng.normal(0, 0.0008)
            now_ms = int(time.time() * 1000)
            for tf in self.timeframes:
                await self._apply_tick_to_candle(symbol, tf, price, now_ms)
            await self.ingest_tick(symbol, price, now_ms)

    async def _apply_tick_to_candle(
        self, symbol: str, tf: str, price: float, now_ms: int
    ) -> None:
        tf_ms = timeframe_seconds(tf) * 1000
        bucket = now_ms // tf_ms * tf_ms
        rows = self.caches[(symbol, tf)].rows
        if rows and rows[-1][0] == bucket:
            last = rows[-1]
            row = [bucket, last[1], max(last[2], price), min(last[3], price), price,
                   last[5] + 1.0]
        else:
            row = [bucket, price, price, price, price, 1.0]
        await self.ingest_candle(symbol, tf, row)


def make_feed(cfg) -> BaseFeed:
    """Fábrica a partir do Config; EXCHANGE_ID=demo ativa o DemoFeed."""
    import os  # noqa: PLC0415

    pairs = cfg.get("market.pairs", ["BTC/USDT"])
    timeframes = cfg.get("market.timeframes", ["4h"])
    throttle = cfg.get("feed.tick_throttle_ms", 1000)
    stale = cfg.get("feed.stale_after_s", 90)
    exchange_id = os.environ.get("EXCHANGE_ID", cfg.get("exchange.id", "binance"))
    if exchange_id == "demo":
        return DemoFeed(pairs, timeframes, throttle, stale)
    return MarketFeed(
        exchange_id,
        pairs,
        timeframes,
        mode=cfg.get("feed.mode", "websocket"),
        tick_throttle_ms=throttle,
        stale_after_s=stale,
    )

"""Motor ao vivo: reage a eventos do feed em vez de fazer polling.

- candle fechado  → análise da estratégia para aquele par×timeframe (sub-segundo)
- tick de preço   → stop/alvos das posições paper avaliados em tempo real
- todos os eventos são publicados num bus interno consumido pelo SSE do dashboard
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from app.alerts import telegram
from app.analysis import strategy
from app.config import PROJECT_ROOT, Config
from app.data.feed import BaseFeed, timeframe_seconds
from app.paper.portfolio import PaperPortfolio
from app.paper.store import Store
from app.risk.manager import RiskManager
from app.scanner import DEDUP_HOURS

log = logging.getLogger(__name__)

BUS_QUEUE_SIZE = 500


class TradingEngine:
    def __init__(self, cfg: Config, feed: BaseFeed):
        self.cfg = cfg
        self.feed = feed
        db_path = PROJECT_ROOT / cfg.get("paper.db_path", "paper_trading.db")
        self.store = Store(db_path)
        self.portfolio = PaperPortfolio(self.store, cfg.get("risk.account_equity", 10000.0))
        self.risk = RiskManager(
            risk_per_trade=cfg.get("risk.risk_per_trade", 0.01),
            min_risk_reward=cfg.get("strategy.min_risk_reward", 1.5),
            max_open_positions=cfg.get("risk.max_open_positions", 5),
        )
        self._subscribers: set[asyncio.Queue] = set()
        self.stats = {"candles_closed": 0, "ticks": 0, "signals": 0, "last_analysis_ms": None}
        feed.on_candle_close(self.handle_candle_close)
        feed.on_tick(self.handle_tick)

    # --- bus de eventos (SSE) ---
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=BUS_QUEUE_SIZE)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # consumidor lento: descarta o mais antigo
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    # --- eventos do feed ---
    async def handle_candle_close(self, symbol: str, timeframe: str, closed_row: list) -> None:
        self.stats["candles_closed"] += 1
        self.publish({
            "type": "candle",
            "symbol": symbol,
            "timeframe": timeframe,
            "tf_seconds": timeframe_seconds(timeframe),
            "candle": {
                "time": closed_row[0] // 1000,
                "open": closed_row[1], "high": closed_row[2],
                "low": closed_row[3], "close": closed_row[4],
                "volume": closed_row[5],
            },
        })
        started = time.perf_counter()
        signal_dict = await asyncio.to_thread(self._analyze_pair, symbol, timeframe)
        self.stats["last_analysis_ms"] = round((time.perf_counter() - started) * 1000, 1)
        if signal_dict is not None:
            self.stats["signals"] += 1
            self.publish({"type": "signal", "signal": signal_dict})
            log.info(
                "SINAL %s %s %s entrada %g (análise em %s ms)",
                signal_dict["direction"].upper(), symbol, timeframe,
                signal_dict["entry"], self.stats["last_analysis_ms"],
            )
            if telegram.is_configured():
                await asyncio.to_thread(telegram.send, telegram.format_signal(signal_dict))

    async def handle_tick(self, symbol: str, price: float, ts_ms: int) -> None:
        self.stats["ticks"] += 1
        self.publish({"type": "tick", "symbol": symbol, "price": price, "ts": ts_ms})
        open_positions = await asyncio.to_thread(self.portfolio.open_positions)
        for pos in open_positions:
            if pos["symbol"] != symbol:
                continue
            updated, changed = await asyncio.to_thread(
                self.portfolio.update_with_tick, pos, price
            )
            if changed:
                self.publish({"type": "position", "position": _position_event(updated)})
                log.info(
                    "posição %s %s: %s @ %g",
                    updated["id"], symbol, updated["events"][-1]["type"], price,
                )

    # --- análise (roda em thread; usa apenas o cache do feed) ---
    def _analyze_pair(self, symbol: str, timeframe: str) -> dict | None:
        df = self.feed.df(symbol, timeframe)
        if len(df) < 60:
            return None
        scfg = self.cfg.get("strategy", {})
        # última linha é o candle em formação: análise usa só os fechados
        signal, _ = strategy.analyze(df.iloc[:-1], symbol, timeframe, scfg)
        if signal is None:
            return None
        dedup_h = DEDUP_HOURS.get(timeframe, 24)
        since = (datetime.now(UTC) - timedelta(hours=dedup_h)).isoformat()
        if self.store.has_recent_signal(symbol, timeframe, signal.direction, since):
            return None
        open_count = len(self.portfolio.open_positions())
        sized = self.risk.apply(signal, self.portfolio.equity, open_count)
        if sized is None:
            return None
        sd = sized.to_dict()
        signal_id = self.store.save_signal(sd)
        self.portfolio.open_from_signal(signal_id, sd)
        return sd

    async def scan_all(self) -> list[dict]:
        """Análise imediata de todos os pares×timeframes a partir do cache."""
        new_signals = []
        for symbol in self.cfg.get("market.pairs", []):
            for timeframe in self.cfg.get("market.timeframes", []):
                sd = await asyncio.to_thread(self._analyze_pair, symbol, timeframe)
                if sd is not None:
                    self.publish({"type": "signal", "signal": sd})
                    new_signals.append(sd)
        return new_signals

    def health(self) -> dict:
        return {"feed": self.feed.health(), "engine": dict(self.stats),
                "sse_subscribers": len(self._subscribers)}


def _position_event(pos: dict) -> dict:
    return {
        "id": pos["id"], "symbol": pos["symbol"], "timeframe": pos["timeframe"],
        "direction": pos["direction"], "status": pos["status"],
        "remaining_size": pos["remaining_size"], "realized_pnl": pos["realized_pnl"],
        "last_event": pos["events"][-1]["type"] if pos["events"] else None,
    }


async def run_live(cfg: Config) -> None:
    """Modo `watch`: feed + engine rodando em primeiro plano."""
    from app.data.feed import make_feed  # noqa: PLC0415

    feed = make_feed(cfg)
    engine = TradingEngine(cfg, feed)
    await feed.start()
    log.info("Engine ao vivo — Ctrl+C para sair")
    try:
        while True:
            await asyncio.sleep(60)
            health = engine.health()
            log.info(
                "ticks=%s candles=%s sinais=%s",
                health["engine"]["ticks"],
                health["engine"]["candles_closed"],
                health["engine"]["signals"],
            )
    finally:
        await feed.stop()

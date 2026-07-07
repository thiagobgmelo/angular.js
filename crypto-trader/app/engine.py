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
    def __init__(self, cfg: Config, feed: BaseFeed, screener=None):
        self.cfg = cfg
        self.feed = feed
        self.screener = screener
        self._refresh_task: asyncio.Task | None = None
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
        for symbol in list(self.feed.pairs):
            for timeframe in self.feed.timeframes:
                sd = await asyncio.to_thread(self._analyze_pair, symbol, timeframe)
                if sd is not None:
                    self.publish({"type": "signal", "signal": sd})
                    new_signals.append(sd)
        return new_signals

    # --- universo dinâmico (screener) ---
    async def refresh_universe(self) -> dict:
        """Re-roda o screener e aplica o diff no feed.

        Proteção: par com posição paper aberta nunca é removido — o stream
        precisa continuar vivo para proteger o stop.
        """
        if self.screener is None:
            return {"added": [], "removed": [], "pairs": list(self.feed.pairs)}
        universe = await asyncio.to_thread(self.screener.screen)
        target = {u["symbol"] for u in universe}
        current = set(self.feed.pairs)
        open_symbols = {
            p["symbol"] for p in await asyncio.to_thread(self.portfolio.open_positions)
        }
        to_add = sorted(target - current)
        to_remove = sorted(current - target - open_symbols)
        kept_open = sorted((current - target) & open_symbols)
        for symbol in to_add:
            await self.feed.add_pair(symbol)
        for symbol in to_remove:
            await self.feed.remove_pair(symbol)
        if kept_open:
            log.info("universo: %s mantidos (posição aberta)", kept_open)
        diff = {"added": to_add, "removed": to_remove, "pairs": list(self.feed.pairs)}
        if to_add or to_remove:
            self.publish({"type": "universe", **diff})
            log.info("universo atualizado: +%s -%s", to_add, to_remove)
        return diff

    def start_refresh_loop(self) -> None:
        if self.screener is None or self._refresh_task is not None:
            return
        hours = self.cfg.get("screener.refresh_hours", 6)

        async def loop():
            while True:
                await asyncio.sleep(hours * 3600)
                try:
                    await self.refresh_universe()
                except Exception:
                    log.exception("falha no refresh do universo")

        self._refresh_task = asyncio.get_running_loop().create_task(loop())

    async def stop_refresh_loop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

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


def build_live_components(cfg: Config) -> tuple[BaseFeed, TradingEngine]:
    """Screener (se habilitado) → feed com o universo → engine. Usado por
    `watch` e pelo lifespan do servidor."""
    from app.data.feed import make_feed  # noqa: PLC0415
    from app.screener import make_screener  # noqa: PLC0415

    screener = None
    pairs = None
    if cfg.get("screener.enabled", True):
        screener = make_screener(cfg)
        universe = screener.screen()
        pairs = [u["symbol"] for u in universe]
    feed = make_feed(cfg, pairs)
    engine = TradingEngine(cfg, feed, screener=screener)
    return feed, engine


async def run_live(cfg: Config) -> None:
    """Modo `watch`: feed + engine rodando em primeiro plano."""
    feed, engine = await asyncio.to_thread(build_live_components, cfg)
    await feed.start()
    engine.start_refresh_loop()
    log.info("Engine ao vivo com %d pares — Ctrl+C para sair", len(feed.pairs))
    try:
        while True:
            await asyncio.sleep(60)
            health = engine.health()
            log.info(
                "pares=%d ticks=%s candles=%s sinais=%s",
                len(feed.pairs),
                health["engine"]["ticks"],
                health["engine"]["candles_closed"],
                health["engine"]["signals"],
            )
    finally:
        await engine.stop_refresh_loop()
        await feed.stop()

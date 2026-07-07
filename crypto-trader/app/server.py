"""API REST + SSE (FastAPI) sobre o feed em tempo real, e estáticos do dashboard.

Endpoints de dados servem do cache em memória do MarketFeed (latência de ms);
`/api/stream` empurra eventos tick/candle/sinal/posição para o navegador.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.analysis import indicators, strategy
from app.backtest import engine as backtest_engine
from app.config import PROJECT_ROOT, load_config
from app.data.exchange import make_client
from app.data.feed import make_feed
from app.engine import TradingEngine

log = logging.getLogger(__name__)

DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
SSE_KEEPALIVE_S = 15

_cfg = load_config()
_ALLOWED_PAIRS: set[str] = set(_cfg.get("market.pairs", []))
_ALLOWED_TIMEFRAMES: set[str] = set(_cfg.get("market.timeframes", []))

_feed = make_feed(_cfg)
_engine = TradingEngine(_cfg, _feed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _feed.start()
    log.info("feed iniciado; dados fluindo em memória")
    yield
    await _feed.stop()


app = FastAPI(title="Crypto Trader", version="2.0", lifespan=lifespan)


def _validate_market(symbol: str, timeframe: str) -> None:
    """Restringe aos pares/timeframes do config (o que o feed transmite)."""
    if symbol not in _ALLOWED_PAIRS:
        raise HTTPException(status_code=422, detail=f"Par não configurado: {symbol!r}")
    if timeframe not in _ALLOWED_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"Timeframe não configurado: {timeframe!r}")


@app.get("/api/config")
def get_config():
    return {
        "exchange": _cfg.get("exchange.id"),
        "pairs": _cfg.get("market.pairs"),
        "timeframes": _cfg.get("market.timeframes"),
    }


@app.get("/api/health")
def get_health():
    """Observabilidade de latência: idade do dado por stream, contadores do engine."""
    return _engine.health()


@app.get("/api/ohlcv")
async def get_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    limit: int = Query(default=300, ge=10, le=1000),
):
    _validate_market(symbol, timeframe)
    df = _feed.df(symbol, timeframe).tail(limit)
    if df.empty:
        raise HTTPException(status_code=503, detail="Feed ainda aquecendo — tente em instantes")
    scfg = _cfg.get("strategy", {})
    df = await asyncio.to_thread(indicators.enrich, df, scfg)
    out = []
    for ts, row in df.iterrows():
        out.append({
            "time": int(ts.timestamp()),
            "open": row["open"], "high": row["high"],
            "low": row["low"], "close": row["close"],
            "volume": row["volume"],
            "ema_fast": None if row.isna()["ema_fast"] else row["ema_fast"],
            "ema_slow": None if row.isna()["ema_slow"] else row["ema_slow"],
        })
    return out


@app.get("/api/analysis")
async def get_analysis(symbol: str = "BTC/USDT", timeframe: str = "4h"):
    """Análise do par a partir do cache (sem I/O de rede)."""
    _validate_market(symbol, timeframe)
    df = _feed.df(symbol, timeframe)
    if len(df) < 60:
        raise HTTPException(status_code=503, detail="Feed ainda aquecendo — tente em instantes")
    scfg = _cfg.get("strategy", {})
    signal, context = await asyncio.to_thread(
        strategy.analyze, df.iloc[:-1], symbol, timeframe, scfg
    )
    if signal is not None:
        signal = _engine.risk.apply(
            signal, _engine.portfolio.equity, len(_engine.portfolio.open_positions())
        )
    return {"signal": signal.to_dict() if signal else None, "context": context}


@app.get("/api/signals")
def get_signals(limit: int = Query(default=50, ge=1, le=500)):
    return _engine.store.recent_signals(limit)


@app.get("/api/portfolio")
def get_portfolio():
    return {
        "summary": _engine.portfolio.summary(),
        "open_positions": _engine.portfolio.open_positions(),
        "closed_positions": _engine.portfolio.closed_positions()[:50],
        "equity_curve": _engine.store.equity_curve(),
    }


@app.post("/api/scan")
async def run_scan():
    """Análise imediata de todos os pares a partir do cache do feed."""
    return {"new_signals": await _engine.scan_all()}


@app.get("/api/stream")
async def stream():
    """SSE: eventos tick/candle/signal/position em tempo real."""
    queue = _engine.subscribe()

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
        finally:
            _engine.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/backtest")
async def run_backtest(
    symbol: str = "BTC/USDT",
    timeframe: str = "4h",
    candles: int = Query(default=1500, ge=300, le=5000),
):
    _validate_market(symbol, timeframe)
    client = make_client(_cfg.get("exchange.id", "binance"))
    try:
        df = await asyncio.to_thread(client.fetch_ohlcv_history, symbol, timeframe, candles)
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar histórico: {err}") from err
    if len(df) < 300:
        raise HTTPException(status_code=422, detail="Histórico insuficiente para backtest")
    return await asyncio.to_thread(
        backtest_engine.run,
        df,
        symbol,
        timeframe,
        _cfg.get("strategy", {}),
        _cfg.get("risk.account_equity", 10000.0),
        _cfg.get("risk.risk_per_trade", 0.01),
    )


@app.get("/")
def index():
    return FileResponse(DASHBOARD_DIR / "index.html")


app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")

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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import auth
from app.analysis import indicators, strategy
from app.backtest import engine as backtest_engine
from app.config import PROJECT_ROOT, load_config
from app.data.exchange import make_client
from app.engine import TradingEngine, build_live_components

log = logging.getLogger(__name__)

DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
SSE_KEEPALIVE_S = 15

_cfg = load_config()
# preenchidos no lifespan (o FastAPI só atende requests após o startup concluir)
_feed = None
_engine: TradingEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _feed, _engine
    # o screener faz chamadas REST síncronas: roda em thread
    _feed, _engine = await asyncio.to_thread(build_live_components, _cfg)
    await _feed.start()
    _engine.start_services()
    log.info("feed iniciado com %d pares; dados fluindo em memória", len(_feed.pairs))
    yield
    await _engine.stop_services()
    await _feed.stop()


app = FastAPI(title="Crypto Trader", version="2.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request, call_next):
    """Exige API_TOKEN em /api/* quando configurado (deploy exposto)."""
    if request.url.path.startswith("/api") and not auth.request_authorized(request):
        return JSONResponse({"detail": "Token de API ausente ou inválido"}, status_code=401)
    return await call_next(request)


def _validate_market(symbol: str, timeframe: str) -> None:
    """Restringe ao universo corrente do feed (dinâmico via screener)."""
    if symbol not in _feed.pairs:
        raise HTTPException(status_code=422, detail=f"Par fora do universo: {symbol!r}")
    if timeframe not in _feed.timeframes:
        raise HTTPException(status_code=422, detail=f"Timeframe não configurado: {timeframe!r}")


@app.get("/api/config")
def get_config():
    return {
        "exchange": _cfg.get("exchange.id"),
        "pairs": list(_feed.pairs),
        "timeframes": list(_feed.timeframes),
        "screener_enabled": bool(_cfg.get("screener.enabled", True)),
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


@app.get("/api/radar")
def get_radar(limit: int = Query(default=100, ge=1, le=1000)):
    """Histórico do radar: oportunidades em formação (log para análise futura)."""
    return _engine.store.recent_radar(limit)


# --- execução real (modos off/manual/auto, aprovações, kill-switch) ---


@app.get("/api/execution")
def get_execution():
    return _engine.execution.status()


@app.post("/api/execution/mode")
async def set_execution_mode(body: dict):
    mode = str(body.get("mode", "")).lower()
    try:
        _engine.execution.set_mode(mode)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"ok": True, "mode": mode}


@app.post("/api/execution/pause")
def pause_execution():
    _engine.execution.pause("API")
    return {"ok": True, "paused": True}


@app.post("/api/execution/resume")
def resume_execution():
    _engine.execution.resume()
    return {"ok": True, "paused": False}


@app.post("/api/approvals/{approval_id}/{decision}")
async def decide_approval(approval_id: int, decision: str):
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decisão deve ser approve|reject")
    result = await _engine.execution.decide(
        approval_id, approve=(decision == "approve"), via="dashboard"
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "falhou"))
    return result


@app.get("/api/execution/log")
def get_execution_log(limit: int = Query(default=100, ge=1, le=1000)):
    return _engine.store.execution_log_recent(limit)


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

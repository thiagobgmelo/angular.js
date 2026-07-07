"""API REST (FastAPI) + arquivos estáticos do dashboard."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.analysis import indicators, strategy
from app.backtest import engine
from app.config import PROJECT_ROOT, load_config
from app.scanner import Scanner

DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

app = FastAPI(title="Crypto Trader", version="1.0")
_cfg = load_config()
_scanner = Scanner(_cfg)


@app.get("/api/config")
def get_config():
    return {
        "exchange": _cfg.get("exchange.id"),
        "pairs": _cfg.get("market.pairs"),
        "timeframes": _cfg.get("market.timeframes"),
    }


@app.get("/api/ohlcv")
def get_ohlcv(symbol: str = "BTC/USDT", timeframe: str = "4h", limit: int = 300):
    try:
        df = _scanner.client.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados: {err}")
    scfg = _cfg.get("strategy", {})
    df = indicators.enrich(df, scfg)
    out = []
    for ts, row in df.iterrows():
        out.append(
            {
                "time": int(ts.timestamp()),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "ema_fast": None if row.isna()["ema_fast"] else row["ema_fast"],
                "ema_slow": None if row.isna()["ema_slow"] else row["ema_slow"],
            }
        )
    return out


@app.get("/api/analysis")
def get_analysis(symbol: str = "BTC/USDT", timeframe: str = "4h"):
    """Análise ao vivo do par: contexto completo + sinal se houver."""
    try:
        df = _scanner.client.fetch_ohlcv(
            symbol, timeframe, limit=_cfg.get("market.candles", 400)
        )
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados: {err}")
    signal, context = strategy.analyze(
        df.iloc[:-1], symbol, timeframe, _cfg.get("strategy", {})
    )
    if signal is not None:
        signal = _scanner.risk.apply(
            signal, _scanner.portfolio.equity, len(_scanner.portfolio.open_positions())
        )
    return {"signal": signal.to_dict() if signal else None, "context": context}


@app.get("/api/signals")
def get_signals(limit: int = 50):
    return _scanner.store.recent_signals(limit)


@app.get("/api/portfolio")
def get_portfolio():
    return {
        "summary": _scanner.portfolio.summary(),
        "open_positions": _scanner.portfolio.open_positions(),
        "closed_positions": _scanner.portfolio.closed_positions()[:50],
        "equity_curve": _scanner.store.equity_curve(),
    }


@app.post("/api/scan")
def run_scan():
    """Dispara uma passada do scanner e retorna os sinais novos."""
    return {"new_signals": _scanner.scan_once(verbose=False)}


@app.get("/api/backtest")
def run_backtest(symbol: str = "BTC/USDT", timeframe: str = "4h", candles: int = 1500):
    try:
        df = _scanner.client.fetch_ohlcv_history(symbol, timeframe, total=candles)
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar histórico: {err}")
    if len(df) < 300:
        raise HTTPException(status_code=422, detail="Histórico insuficiente para backtest")
    return engine.run(
        df,
        symbol,
        timeframe,
        _cfg.get("strategy", {}),
        initial_equity=_cfg.get("risk.account_equity", 10000.0),
        risk_per_trade=_cfg.get("risk.risk_per_trade", 0.01),
    )


@app.get("/")
def index():
    return FileResponse(DASHBOARD_DIR / "index.html")


app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")

"""Backtest da estratégia de confluência sobre histórico OHLCV.

Percorre o histórico candle a candle: analisa a janela até o candle i,
abre no fechamento quando há sinal e simula a gestão (TP1 50% + breakeven,
TP2 25%, TP3/stop) nos candles seguintes. Conservador: stop antes de alvo
quando ambos ocorrem no mesmo candle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.analysis import strategy
from app.risk.manager import RiskManager

TP1_FRACTION = 0.5
TP2_FRACTION = 0.25


@dataclass
class BacktestTrade:
    opened_at: str
    direction: str
    entry: float
    stop: float
    targets: list[float]
    size: float
    pnl: float = 0.0
    closed_at: str | None = None
    exit_reason: str | None = None
    events: list[str] = field(default_factory=list)


def run(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    cfg: dict,
    initial_equity: float = 10000.0,
    risk_per_trade: float = 0.01,
    warmup: int = 250,
    step: int = 1,
) -> dict:
    rm = RiskManager(risk_per_trade=risk_per_trade, min_risk_reward=cfg.get("min_risk_reward", 1.5), max_open_positions=1)
    equity = initial_equity
    trades: list[BacktestTrade] = []
    open_trade: BacktestTrade | None = None
    tp_state: set[str] = set()
    equity_curve: list[tuple[str, float]] = []

    for i in range(warmup, len(df), 1):
        candle = df.iloc[i]
        ts = str(df.index[i])

        # 1. gerencia posição aberta com o candle atual
        if open_trade is not None:
            is_long = open_trade.direction == "long"
            stop, (tp1, tp2, tp3) = open_trade.stop, open_trade.targets[:3]
            remaining_frac = 1.0 - (TP1_FRACTION if "tp1" in tp_state else 0) - (
                TP2_FRACTION if "tp2" in tp_state else 0
            )

            def pnl(price: float, frac: float) -> float:
                units = open_trade.size * frac
                return (price - open_trade.entry) * units if is_long else (open_trade.entry - price) * units

            hit_stop = candle["low"] <= stop if is_long else candle["high"] >= stop
            def hit(level: float) -> bool:
                return candle["high"] >= level if is_long else candle["low"] <= level

            if hit_stop:
                open_trade.pnl += pnl(stop, remaining_frac)
                open_trade.exit_reason = "stop" if "tp1" not in tp_state else "stop_breakeven"
                open_trade.closed_at = ts
                equity += open_trade.pnl
                trades.append(open_trade)
                equity_curve.append((ts, equity))
                open_trade, tp_state = None, set()
            else:
                if "tp1" not in tp_state and hit(tp1):
                    open_trade.pnl += pnl(tp1, TP1_FRACTION)
                    open_trade.stop = open_trade.entry
                    open_trade.events.append("tp1+breakeven")
                    tp_state.add("tp1")
                if "tp1" in tp_state and "tp2" not in tp_state and hit(tp2):
                    open_trade.pnl += pnl(tp2, TP2_FRACTION)
                    open_trade.events.append("tp2")
                    tp_state.add("tp2")
                if "tp2" in tp_state and hit(tp3):
                    frac = 1.0 - TP1_FRACTION - TP2_FRACTION
                    open_trade.pnl += pnl(tp3, frac)
                    open_trade.exit_reason = "tp3"
                    open_trade.closed_at = ts
                    equity += open_trade.pnl
                    trades.append(open_trade)
                    equity_curve.append((ts, equity))
                    open_trade, tp_state = None, set()

        # 2. procura novo sinal no fechamento (sem posição aberta)
        if open_trade is None and i % step == 0:
            window = df.iloc[: i + 1].tail(400)
            signal, _ = strategy.analyze(window, symbol, timeframe, cfg)
            if signal is not None:
                sized = rm.apply(signal, equity, open_positions=0)
                if sized is not None:
                    open_trade = BacktestTrade(
                        opened_at=ts,
                        direction=sized.direction,
                        entry=sized.entry,
                        stop=sized.stop,
                        targets=sized.targets,
                        size=sized.position_size,
                    )
                    tp_state = set()

    # posição ainda aberta ao final: fecha a mercado no último close
    if open_trade is not None:
        last_close = float(df["close"].iloc[-1])
        is_long = open_trade.direction == "long"
        remaining_frac = 1.0 - (TP1_FRACTION if "tp1" in tp_state else 0) - (
            TP2_FRACTION if "tp2" in tp_state else 0
        )
        units = open_trade.size * remaining_frac
        open_trade.pnl += (last_close - open_trade.entry) * units if is_long else (open_trade.entry - last_close) * units
        open_trade.exit_reason = "end_of_data"
        open_trade.closed_at = str(df.index[-1])
        equity += open_trade.pnl
        trades.append(open_trade)
        equity_curve.append((str(df.index[-1]), equity))

    return _metrics(trades, initial_equity, equity, equity_curve, symbol, timeframe)


def _metrics(
    trades: list[BacktestTrade],
    initial: float,
    final: float,
    curve: list[tuple[str, float]],
    symbol: str,
    timeframe: str,
) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))

    peak, max_dd = initial, 0.0
    for _, value in curve:
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "initial_equity": initial,
        "final_equity": round(final, 2),
        "return_pct": round((final / initial - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "equity_curve": [{"at": at, "value": round(v, 2)} for at, v in curve],
        "trade_list": [
            {
                "opened_at": t.opened_at,
                "closed_at": t.closed_at,
                "direction": t.direction,
                "entry": t.entry,
                "exit_reason": t.exit_reason,
                "pnl": round(t.pnl, 2),
            }
            for t in trades
        ],
    }

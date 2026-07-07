"""Indicadores técnicos clássicos implementados em pandas/numpy.

Fórmulas padrão da literatura (Wilder, Appel, Bollinger) — mesmas usadas
por TradingView e TA-Lib, sem dependências binárias.
"""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder (suavização RMA, como no TradingView)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    return out.where(avg_loss != 0, 100.0)


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }
    )


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder). Espera colunas high/low/close."""
    prev_close = df["close"].shift()
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    std = series.rolling(period).std(ddof=0)
    return pd.DataFrame(
        {
            "bb_upper": mid + num_std * std,
            "bb_mid": mid,
            "bb_lower": mid - num_std * std,
        }
    )


def volume_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df["volume"].rolling(period).mean()


def enrich(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Anexa todos os indicadores usados pela estratégia ao DataFrame OHLCV."""
    cfg = cfg or {}
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.get("ema_fast", 50))
    out["ema_slow"] = ema(out["close"], cfg.get("ema_slow", 200))
    out["rsi"] = rsi(out["close"], cfg.get("rsi_period", 14))
    out = out.join(macd(out["close"]))
    out["atr"] = atr(out, cfg.get("atr_period", 14))
    out = out.join(bollinger(out["close"], cfg.get("bb_period", 20), cfg.get("bb_std", 2.0)))
    out["volume_ma"] = volume_ma(out, cfg.get("volume_ma", 20))
    return out

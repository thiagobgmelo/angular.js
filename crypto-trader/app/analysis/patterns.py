"""Padrões de candlestick clássicos (Nison). Avaliados no último candle fechado."""
from __future__ import annotations

import pandas as pd


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def is_bullish_engulfing(prev: pd.Series, cur: pd.Series) -> bool:
    return (
        prev["close"] < prev["open"]
        and cur["close"] > cur["open"]
        and cur["close"] >= prev["open"]
        and cur["open"] <= prev["close"]
        and _body(cur["open"], cur["close"]) > _body(prev["open"], prev["close"])
    )


def is_bearish_engulfing(prev: pd.Series, cur: pd.Series) -> bool:
    return (
        prev["close"] > prev["open"]
        and cur["close"] < cur["open"]
        and cur["open"] >= prev["close"]
        and cur["close"] <= prev["open"]
        and _body(cur["open"], cur["close"]) > _body(prev["open"], prev["close"])
    )


def is_hammer(cur: pd.Series) -> bool:
    """Corpo pequeno no topo, sombra inferior >= 2x o corpo (reversão de alta)."""
    body = _body(cur["open"], cur["close"])
    rng = _range(cur["high"], cur["low"])
    lower_shadow = min(cur["open"], cur["close"]) - cur["low"]
    upper_shadow = cur["high"] - max(cur["open"], cur["close"])
    return (
        body / rng < 0.35
        and lower_shadow >= 2 * body
        and upper_shadow <= body
    )


def is_shooting_star(cur: pd.Series) -> bool:
    """Corpo pequeno embaixo, sombra superior >= 2x o corpo (reversão de baixa)."""
    body = _body(cur["open"], cur["close"])
    rng = _range(cur["high"], cur["low"])
    lower_shadow = min(cur["open"], cur["close"]) - cur["low"]
    upper_shadow = cur["high"] - max(cur["open"], cur["close"])
    return (
        body / rng < 0.35
        and upper_shadow >= 2 * body
        and lower_shadow <= body
    )


def is_doji(cur: pd.Series) -> bool:
    return _body(cur["open"], cur["close"]) / _range(cur["high"], cur["low"]) < 0.1


def detect(df: pd.DataFrame) -> list[str]:
    """Padrões presentes no último candle fechado do DataFrame."""
    if len(df) < 2:
        return []
    prev, cur = df.iloc[-2], df.iloc[-1]
    found = []
    if is_bullish_engulfing(prev, cur):
        found.append("bullish_engulfing")
    if is_bearish_engulfing(prev, cur):
        found.append("bearish_engulfing")
    if is_hammer(cur):
        found.append("hammer")
    if is_shooting_star(cur):
        found.append("shooting_star")
    if is_doji(cur):
        found.append("doji")
    return found


BULLISH = {"bullish_engulfing", "hammer"}
BEARISH = {"bearish_engulfing", "shooting_star"}

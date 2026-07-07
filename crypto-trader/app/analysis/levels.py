"""Suporte/resistência por fractais de swing, zonas e Fibonacci."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Zone:
    """Zona de suporte/resistência agrupando pivôs próximos."""

    price: float      # preço médio da zona
    touches: int      # quantos pivôs formaram a zona
    kind: str         # "support" | "resistance" (relativo ao preço atual)


def swing_points(df: pd.DataFrame, lookback: int = 5) -> tuple[pd.Series, pd.Series]:
    """Fractais: swing high/low confirmados por `lookback` candles de cada lado.

    Retorna (highs, lows) como Series esparsas indexadas pelo timestamp do pivô.
    """
    highs, lows = {}, {}
    hi, lo = df["high"].values, df["low"].values
    n = len(df)
    for i in range(lookback, n - lookback):
        window_h = hi[i - lookback : i + lookback + 1]
        window_l = lo[i - lookback : i + lookback + 1]
        # empates (ex.: abertura igual ao fechamento anterior) contam uma única
        # vez: o pivô é a primeira ocorrência do extremo dentro da janela
        if hi[i] == window_h.max() and window_h.argmax() == lookback:
            highs[df.index[i]] = hi[i]
        if lo[i] == window_l.min() and window_l.argmin() == lookback:
            lows[df.index[i]] = lo[i]
    return pd.Series(highs, dtype=float), pd.Series(lows, dtype=float)


def sr_zones(
    df: pd.DataFrame, lookback: int = 5, tolerance: float = 0.005
) -> list[Zone]:
    """Agrupa pivôs em zonas: preços a menos de `tolerance` (fração) se fundem."""
    highs, lows = swing_points(df, lookback)
    pivots = sorted(list(highs.values) + list(lows.values))
    if not pivots:
        return []

    clusters: list[list[float]] = [[pivots[0]]]
    for p in pivots[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    last_close = float(df["close"].iloc[-1])
    zones = []
    for cluster in clusters:
        price = sum(cluster) / len(cluster)
        kind = "support" if price < last_close else "resistance"
        zones.append(Zone(price=price, touches=len(cluster), kind=kind))
    return zones


def nearest_zones(zones: list[Zone], price: float) -> tuple[Zone | None, Zone | None]:
    """(suporte mais próximo abaixo, resistência mais próxima acima)."""
    supports = [z for z in zones if z.price < price]
    resistances = [z for z in zones if z.price > price]
    support = max(supports, key=lambda z: z.price) if supports else None
    resistance = min(resistances, key=lambda z: z.price) if resistances else None
    return support, resistance


def in_zone(price: float, zone: Zone | None, tolerance: float = 0.005) -> bool:
    return zone is not None and abs(price - zone.price) / zone.price <= tolerance


def fibonacci_retracement(df: pd.DataFrame, lookback: int = 5) -> dict[str, float]:
    """Níveis de Fibonacci da última perna (swing low↔high mais recentes).

    Retorna {} se não houver dois pivôs para definir a perna.
    """
    highs, lows = swing_points(df, lookback)
    if highs.empty or lows.empty:
        return {}
    last_high_ts, last_low_ts = highs.index[-1], lows.index[-1]
    high, low = float(highs.iloc[-1]), float(lows.iloc[-1])
    diff = high - low
    if diff <= 0:
        return {}
    uptrend_leg = last_low_ts < last_high_ts  # perna de alta: low veio antes do high
    levels = {}
    for ratio in (0.382, 0.5, 0.618):
        if uptrend_leg:
            levels[f"{ratio:.3f}"] = high - diff * ratio   # retração da alta
        else:
            levels[f"{ratio:.3f}"] = low + diff * ratio    # retração da baixa
    levels["0.0"] = high if uptrend_leg else low
    levels["1.0"] = low if uptrend_leg else high
    return levels


def market_structure(df: pd.DataFrame, lookback: int = 5) -> str:
    """Estrutura de mercado (Teoria de Dow) pelos 2 últimos swings de cada tipo.

    "uptrend": topos e fundos ascendentes; "downtrend": descendentes;
    "range" caso contrário ou sem pivôs suficientes.
    """
    highs, lows = swing_points(df, lookback)
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    hh = highs.iloc[-1] > highs.iloc[-2]
    hl = lows.iloc[-1] > lows.iloc[-2]
    lh = highs.iloc[-1] < highs.iloc[-2]
    ll = lows.iloc[-1] < lows.iloc[-2]
    if hh and hl:
        return "uptrend"
    if lh and ll:
        return "downtrend"
    return "range"

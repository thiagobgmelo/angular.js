"""Motor de confluência: combina tendência, momentum, S/R, padrões e volume.

Teorias aplicadas (todas clássicas e amplamente documentadas):
- Teoria de Dow: estrutura de topos/fundos define a tendência vigente
- Seguimento de tendência: EMA 50/200 (posição do preço e cruzamento)
- Momentum: RSI de Wilder (zonas + divergência) e MACD de Appel
- Ação do preço: padrões de candle de Nison em zonas de S/R
- Volatilidade: Bandas de Bollinger (extremos) e ATR (dimensionamento de stop)
- Volume: confirmação (acima da média de 20 períodos)

Cada critério a favor soma 1 ponto. Sinal só é emitido quando o score
atinge o limiar configurado E o R:R até o TP1 respeita o mínimo.
"""
from __future__ import annotations

import pandas as pd

from . import indicators, levels, patterns
from .signal import Signal, trade_type_for

MAX_SCORE = 7


def _rsi_divergence(df: pd.DataFrame, lookback: int = 5) -> str | None:
    """Divergência simples: preço faz novo extremo, RSI não acompanha."""
    highs, lows = levels.swing_points(df, lookback)
    if len(lows) >= 2:
        p1, p2 = lows.index[-2], lows.index[-1]
        if lows.iloc[-1] < lows.iloc[-2] and df.loc[p2, "rsi"] > df.loc[p1, "rsi"]:
            return "bullish"
    if len(highs) >= 2:
        p1, p2 = highs.index[-2], highs.index[-1]
        if highs.iloc[-1] > highs.iloc[-2] and df.loc[p2, "rsi"] < df.loc[p1, "rsi"]:
            return "bearish"
    return None


def analyze(
    df: pd.DataFrame, symbol: str, timeframe: str, cfg: dict
) -> tuple[Signal | None, dict]:
    """Analisa um DataFrame OHLCV e retorna (sinal ou None, contexto da análise).

    O contexto é retornado sempre, para exibição no dashboard mesmo sem sinal.
    """
    scfg = cfg
    lookback = scfg.get("swing_lookback", 5)
    tolerance = scfg.get("sr_zone_tolerance", 0.005)

    df = indicators.enrich(df, scfg)
    last = df.iloc[-1]
    price = float(last["close"])

    structure = levels.market_structure(df, lookback)
    zones = levels.sr_zones(df, lookback, tolerance)
    support, resistance = levels.nearest_zones(zones, price)
    fib = levels.fibonacci_retracement(df, lookback)
    candle_patterns = patterns.detect(df)
    divergence = _rsi_divergence(df, lookback)

    long_score, short_score = 0, 0
    long_why, short_why = [], []
    criteria: list[dict] = []

    def crit(cid: str, label: str, long_ok: bool, short_ok: bool, value: str,
             long_reason: str | None = None, short_reason: str | None = None) -> None:
        """Registra o critério no checklist e aplica pontuação/racional."""
        nonlocal long_score, short_score
        criteria.append({
            "id": cid, "label": label,
            "long": bool(long_ok), "short": bool(short_ok), "value": value,
        })
        if long_ok:
            long_score += 1
            long_why.append(long_reason or label)
        if short_ok:
            short_score += 1
            short_why.append(short_reason or label)

    # 1. Estrutura de mercado (Dow)
    crit(
        "structure", "Estrutura de mercado (Dow)",
        structure == "uptrend", structure == "downtrend",
        {"uptrend": "alta", "downtrend": "baixa", "range": "lateral"}[structure],
        "Estrutura de alta (topos e fundos ascendentes)",
        "Estrutura de baixa (topos e fundos descendentes)",
    )

    # 2. Tendência por EMAs
    ema_ok = pd.notna(last["ema_slow"])
    crit(
        "emas", "Tendência EMA 50/200",
        ema_ok and price > last["ema_fast"] > last["ema_slow"],
        ema_ok and price < last["ema_fast"] < last["ema_slow"],
        f"EMA50 {last['ema_fast']:.6g} · EMA200 {last['ema_slow']:.6g}" if ema_ok else "aquecendo",
        "Preço acima das EMAs 50>200 (tendência de alta)",
        "Preço abaixo das EMAs 50<200 (tendência de baixa)",
    )

    # 3. RSI: zona + divergência. Extremo de RSI contra a tendência vigente não
    # pontua ("sobrevendido pode continuar sobrevendido"); reversão exige divergência.
    rsi_val = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0
    rsi_long = rsi_val <= scfg.get("rsi_oversold", 30) and structure != "downtrend"
    rsi_short = rsi_val >= scfg.get("rsi_overbought", 70) and structure != "uptrend"
    div_note = f" · divergência de {'alta' if divergence == 'bullish' else 'baixa'}"
    rsi_value = f"RSI {rsi_val:.1f}" + (div_note if divergence else "")
    crit(
        "rsi", "RSI (zona + divergência)",
        rsi_long or divergence == "bullish",
        rsi_short or divergence == "bearish",
        rsi_value,
        f"RSI {rsi_val:.0f} sobrevendido" if rsi_long else "Divergência de alta no RSI",
        f"RSI {rsi_val:.0f} sobrecomprado" if rsi_short else "Divergência de baixa no RSI",
    )

    # 4. MACD
    macd_ok = pd.notna(last["macd"])
    crit(
        "macd", "MACD (12,26,9)",
        macd_ok and last["macd"] > last["signal"] and last["histogram"] > 0,
        macd_ok and last["macd"] < last["signal"] and last["histogram"] < 0,
        f"histograma {last['histogram']:+.4g}" if macd_ok else "aquecendo",
        "MACD acima da linha de sinal",
        "MACD abaixo da linha de sinal",
    )

    # 5. Localização: preço em zona de S/R ou nível de Fibonacci
    at_support = levels.in_zone(price, support, tolerance * 2)
    at_resistance = levels.in_zone(price, resistance, tolerance * 2)
    near_fib = any(
        abs(price - lvl) / price <= tolerance
        for key, lvl in fib.items()
        if key in ("0.382", "0.500", "0.618")
    )
    loc_bits = []
    if at_support:
        loc_bits.append("em suporte")
    if at_resistance:
        loc_bits.append("em resistência")
    if near_fib:
        loc_bits.append("em nível de Fibonacci")
    crit(
        "location", "Localização (S/R + Fibonacci)",
        at_support or (near_fib and structure == "uptrend"),
        at_resistance or (near_fib and structure == "downtrend"),
        ", ".join(loc_bits) or "longe de zonas de interesse",
        "Preço testando zona de suporte" if at_support
        else "Preço em retração de Fibonacci na tendência de alta",
        "Preço testando zona de resistência" if at_resistance
        else "Preço em retração de Fibonacci na tendência de baixa",
    )

    # 6. Padrão de candle (vale mais quando em zona relevante)
    bullish_pattern = patterns.BULLISH.intersection(candle_patterns)
    bearish_pattern = patterns.BEARISH.intersection(candle_patterns)
    crit(
        "pattern", "Padrão de candle (Nison)",
        bool(bullish_pattern and (at_support or near_fib or rsi_val <= 40)),
        bool(bearish_pattern and (at_resistance or near_fib or rsi_val >= 60)),
        ", ".join(candle_patterns) or "nenhum",
        f"Padrão de reversão de alta ({', '.join(bullish_pattern)}) em região de interesse",
        f"Padrão de reversão de baixa ({', '.join(bearish_pattern)}) em região de interesse",
    )

    # 7. Volume confirmando o último candle
    vol_ok = pd.notna(last["volume_ma"]) and last["volume"] > last["volume_ma"]
    vol_ratio = (
        f"{last['volume'] / last['volume_ma']:.1f}× a média"
        if pd.notna(last["volume_ma"]) and last["volume_ma"] > 0 else "aquecendo"
    )
    crit(
        "volume", "Volume de confirmação",
        bool(vol_ok and last["close"] > last["open"]),
        bool(vol_ok and last["close"] < last["open"]),
        vol_ratio,
        "Volume acima da média confirmando candle de alta",
        "Volume acima da média confirmando candle de baixa",
    )

    context = {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "structure": structure,
        "rsi": round(rsi_val, 1),
        "macd_histogram": float(last["histogram"]) if pd.notna(last["histogram"]) else None,
        "atr": float(last["atr"]) if pd.notna(last["atr"]) else None,
        "support": support.price if support else None,
        "resistance": resistance.price if resistance else None,
        "zones": [
            {"price": z.price, "touches": z.touches, "kind": z.kind} for z in zones
        ],
        "fibonacci": fib,
        "patterns": candle_patterns,
        "criteria": criteria,
        "long_score": long_score,
        "short_score": short_score,
        "max_score": MAX_SCORE,
        "long_rationale": long_why,
        "short_rationale": short_why,
    }

    threshold = scfg.get("score_threshold", 4)
    direction = None
    # exige dominância clara de um lado, não só atingir o limiar
    if long_score >= threshold and long_score >= short_score + 2:
        direction = "long"
    elif short_score >= threshold and short_score >= long_score + 2:
        direction = "short"
    if direction is None:
        return None, context

    signal = _build_signal(
        df, symbol, timeframe, direction, price,
        support, resistance, zones,
        long_score if direction == "long" else short_score,
        long_why if direction == "long" else short_why,
        scfg,
    )
    return signal, context


def _build_signal(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    direction: str,
    price: float,
    support: levels.Zone | None,
    resistance: levels.Zone | None,
    zones: list[levels.Zone],
    score: int,
    rationale: list[str],
    scfg: dict,
) -> Signal | None:
    """Stop no swing recente ± 1 ATR; alvos em 1.5R/2.5R e na próxima zona."""
    atr_val = float(df["atr"].iloc[-1])
    highs, lows = levels.swing_points(df, scfg.get("swing_lookback", 5))

    if direction == "long":
        swing_stop = float(lows.iloc[-1]) if len(lows) else price - 2 * atr_val
        stop = min(swing_stop, price - atr_val) - 0.1 * atr_val
        risk = price - stop
        tp1 = price + 1.5 * risk
        tp2 = price + 2.5 * risk
        above = [z.price for z in zones if z.price > tp2 * 1.001]
        tp3 = min(above) if above else price + 4 * risk
    else:
        swing_stop = float(highs.iloc[-1]) if len(highs) else price + 2 * atr_val
        stop = max(swing_stop, price + atr_val) + 0.1 * atr_val
        risk = stop - price
        tp1 = price - 1.5 * risk
        tp2 = price - 2.5 * risk
        below = [z.price for z in zones if 0 < z.price < tp2 * 0.999]
        tp3 = max(below) if below else price - 4 * risk
        if tp3 <= 0:
            tp3 = price - 4 * risk if price > 4 * risk else price * 0.5

    if risk <= 0:
        return None

    signal = Signal(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        trade_type=trade_type_for(timeframe),
        entry=price,
        stop=round(stop, 8),
        targets=[round(tp1, 8), round(tp2, 8), round(tp3, 8)],
        score=score,
        max_score=MAX_SCORE,
        rationale=rationale,
    )
    if signal.rr_to(signal.targets[0]) < scfg.get("min_risk_reward", 1.5):
        return None
    return signal

"""Gestão de risco: position sizing por risco fixo, alavancagem e validação.

Sobre alavancagem (perpétuos): com o tamanho de posição derivado do stop
(risco fixo em % do capital), a alavancagem NÃO altera o risco do trade —
ela define quanta margem fica imobilizada e onde cai o preço de liquidação.
A sugestão maximiza a eficiência de margem sujeita à regra de segurança:
a liquidação deve ficar pelo menos `liq_buffer`× além da distância do stop.

Aproximação (margem isolada): dist_liquidação ≈ 1/alavancagem − mmr.
Exigindo dist_liq ≥ buffer × dist_stop:  alav ≤ 1 / (buffer×dist_stop + mmr)
"""
from __future__ import annotations

import math

from app.analysis.signal import Signal

MAINTENANCE_MARGIN_RATE = 0.005  # ~0,5% típico dos perpétuos USDT (aprox.)


class RiskManager:
    def __init__(
        self,
        risk_per_trade: float = 0.01,
        min_risk_reward: float = 1.5,
        max_open_positions: int = 5,
        max_leverage: int = 10,
        liq_buffer: float = 3.0,
    ):
        self.risk_per_trade = risk_per_trade
        self.min_risk_reward = min_risk_reward
        self.max_open_positions = max_open_positions
        self.max_leverage = max_leverage
        self.liq_buffer = liq_buffer

    def position_size(self, equity: float, entry: float, stop: float) -> float:
        """Unidades do ativo tal que (entry-stop)*size == equity*risco%."""
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0 or equity <= 0:
            return 0.0
        return (equity * self.risk_per_trade) / risk_per_unit

    def suggest_leverage(self, entry: float, stop: float) -> tuple[int, float | None, str]:
        """(alavancagem, preço de liquidação estimado, racional legível).

        Direção é inferida pela posição do stop (stop < entry ⇒ long).
        """
        stop_dist = abs(entry - stop) / entry
        if stop_dist <= 0:
            return 1, None, "stop inválido — alavancagem mínima"
        safe = 1.0 / (self.liq_buffer * stop_dist + MAINTENANCE_MARGIN_RATE)
        lev = max(1, min(int(math.floor(safe)), self.max_leverage))
        liq_dist = 1.0 / lev - MAINTENANCE_MARGIN_RATE
        is_long = stop < entry
        liq_price = entry * (1 - liq_dist) if is_long else entry * (1 + liq_dist)
        capped = int(math.floor(safe)) > self.max_leverage
        rationale = (
            f"Stop a {stop_dist * 100:.1f}% da entrada → liquidação estimada a "
            f"{liq_dist * 100:.1f}% ({liq_dist / stop_dist:.1f}× a distância do stop). "
            + (
                f"Teto de {self.max_leverage}x aplicado (regra permitiria {int(safe)}x)."
                if capped
                else f"Maior alavancagem que mantém a liquidação ≥ {self.liq_buffer:g}× o stop."
            )
        )
        return lev, round(liq_price, 8), rationale

    def apply(self, signal: Signal, equity: float, open_positions: int = 0) -> Signal | None:
        """Valida o sinal e preenche tamanho de posição e alavancagem; None se reprovado."""
        if open_positions >= self.max_open_positions:
            return None
        if not signal.targets or signal.rr_to(signal.targets[0]) < self.min_risk_reward:
            return None
        size = self.position_size(equity, signal.entry, signal.stop)
        if size <= 0:
            return None
        signal.position_size = round(size, 8)
        signal.risk_amount = round(equity * self.risk_per_trade, 2)
        lev, liq_price, why = self.suggest_leverage(signal.entry, signal.stop)
        signal.suggested_leverage = lev
        signal.liquidation_price_est = liq_price
        signal.leverage_rationale = why
        signal.margin_required = round(size * signal.entry / lev, 2)
        return signal

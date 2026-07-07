"""Gestão de risco: position sizing por risco fixo e validação de sinais."""
from __future__ import annotations

from app.analysis.signal import Signal


class RiskManager:
    def __init__(
        self,
        risk_per_trade: float = 0.01,
        min_risk_reward: float = 1.5,
        max_open_positions: int = 5,
    ):
        self.risk_per_trade = risk_per_trade
        self.min_risk_reward = min_risk_reward
        self.max_open_positions = max_open_positions

    def position_size(self, equity: float, entry: float, stop: float) -> float:
        """Unidades do ativo tal que (entry-stop)*size == equity*risco%."""
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0 or equity <= 0:
            return 0.0
        return (equity * self.risk_per_trade) / risk_per_unit

    def apply(self, signal: Signal, equity: float, open_positions: int = 0) -> Signal | None:
        """Valida o sinal e preenche tamanho de posição; None se reprovado."""
        if open_positions >= self.max_open_positions:
            return None
        if not signal.targets or signal.rr_to(signal.targets[0]) < self.min_risk_reward:
            return None
        size = self.position_size(equity, signal.entry, signal.stop)
        if size <= 0:
            return None
        signal.position_size = round(size, 8)
        signal.risk_amount = round(equity * self.risk_per_trade, 2)
        return signal

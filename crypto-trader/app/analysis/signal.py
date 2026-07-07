"""Modelo de sinal de trade."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

SWING_TIMEFRAMES = {"4h", "6h", "8h", "12h", "1d", "3d", "1w"}


def trade_type_for(timeframe: str) -> str:
    return "swing" if timeframe in SWING_TIMEFRAMES else "day_trade"


@dataclass
class Signal:
    symbol: str
    timeframe: str
    direction: str                  # "long" | "short"
    trade_type: str                 # "swing" | "day_trade"
    entry: float
    stop: float
    targets: list[float]            # [TP1, TP2, TP3]
    score: int                      # pontos de confluência a favor
    max_score: int                  # máximo possível
    position_size: float = 0.0      # unidades do ativo (definido pelo risk manager)
    risk_amount: float = 0.0        # valor em quote arriscado
    rationale: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    def rr_to(self, target: float) -> float:
        if self.risk_per_unit == 0:
            return 0.0
        return abs(target - self.entry) / self.risk_per_unit

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rr_tp1"] = round(self.rr_to(self.targets[0]), 2) if self.targets else None
        return d

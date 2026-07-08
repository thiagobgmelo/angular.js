"""Modelo de sinal de trade."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

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
    suggested_leverage: int = 1     # alavancagem sugerida (perpétuos; ver RiskManager)
    margin_required: float = 0.0    # margem imobilizada com a alavancagem sugerida
    liquidation_price_est: float | None = None  # estimativa (margem isolada)
    leverage_rationale: str = ""
    context: dict = field(default_factory=dict)  # snapshot do cenário da análise
    rationale: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
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

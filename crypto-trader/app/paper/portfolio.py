"""Carteira de paper trading.

Regras de execução simulada (espelham a gestão do risk manager):
- Abre posição a mercado no preço de entrada do sinal
- TP1 atingido → realiza 50% da posição e move o stop para breakeven
- TP2 atingido → realiza mais 25%
- TP3 atingido ou stop → fecha o restante
Preenchimentos usam high/low do candle (intrabar) de forma conservadora:
se stop e alvo saem no mesmo candle, assume que o STOP veio primeiro.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .store import Store

TP1_FRACTION = 0.5
TP2_FRACTION = 0.25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperPortfolio:
    def __init__(self, store: Store, initial_equity: float = 10000.0):
        self.store = store
        self.initial_equity = initial_equity

    @property
    def equity(self) -> float:
        return self.initial_equity + self.store.realized_pnl_total()

    def open_from_signal(self, signal_id: int, signal_dict: dict) -> int:
        pos_id = self.store.open_position(signal_id, signal_dict)
        self.store.record_equity(_now(), self.equity)
        return pos_id

    def open_positions(self) -> list[dict]:
        return self.store.positions(status="open")

    def closed_positions(self) -> list[dict]:
        return self.store.positions(status="closed")

    def update_with_candle(self, pos: dict, high: float, low: float) -> dict:
        """Processa um candle contra uma posição aberta. Retorna a posição atualizada."""
        direction = pos["direction"]
        entry, stop = pos["entry"], pos["stop"]
        tp1, tp2, tp3 = pos["targets"][:3]
        events: list = list(pos["events"])
        remaining = pos["remaining_size"]
        realized = pos["realized_pnl"]
        is_long = direction == "long"

        def hit_stop() -> bool:
            return low <= stop if is_long else high >= stop

        def hit(level: float) -> bool:
            return high >= level if is_long else low <= level

        def pnl(exit_price: float, size: float) -> float:
            return (exit_price - entry) * size if is_long else (entry - exit_price) * size

        done = False
        # conservador: stop primeiro
        if hit_stop():
            realized += pnl(stop, remaining)
            events.append({"at": _now(), "type": "stop" if "tp1" not in [e.get("type") for e in events] else "stop_breakeven", "price": stop})
            remaining = 0.0
            done = True
        else:
            hit_types = [e.get("type") for e in events]
            if "tp1" not in hit_types and hit(tp1):
                part = pos["size"] * TP1_FRACTION
                part = min(part, remaining)
                realized += pnl(tp1, part)
                remaining -= part
                stop = entry  # breakeven
                events.append({"at": _now(), "type": "tp1", "price": tp1})
                events.append({"at": _now(), "type": "breakeven", "price": entry})
                hit_types.append("tp1")
            if "tp2" not in hit_types and "tp1" in hit_types and hit(tp2):
                part = min(pos["size"] * TP2_FRACTION, remaining)
                realized += pnl(tp2, part)
                remaining -= part
                events.append({"at": _now(), "type": "tp2", "price": tp2})
                hit_types.append("tp2")
            if "tp2" in hit_types and hit(tp3):
                realized += pnl(tp3, remaining)
                remaining = 0.0
                events.append({"at": _now(), "type": "tp3", "price": tp3})
                done = True

        fields: dict = {
            "remaining_size": remaining,
            "realized_pnl": realized,
            "stop": stop,
            "events": events,
        }
        if done or remaining <= 1e-12:
            fields["status"] = "closed"
            fields["closed_at"] = _now()
        self.store.update_position(pos["id"], **fields)
        if fields.get("status") == "closed":
            self.store.record_equity(_now(), self.equity)
        updated = dict(pos)
        updated.update(fields)
        return updated

    def summary(self) -> dict:
        closed = self.closed_positions()
        wins = [p for p in closed if p["realized_pnl"] > 0]
        losses = [p for p in closed if p["realized_pnl"] <= 0]
        gross_win = sum(p["realized_pnl"] for p in wins)
        gross_loss = abs(sum(p["realized_pnl"] for p in losses))
        return {
            "initial_equity": self.initial_equity,
            "equity": round(self.equity, 2),
            "realized_pnl": round(self.store.realized_pnl_total(), 2),
            "open_positions": len(self.open_positions()),
            "closed_trades": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        }

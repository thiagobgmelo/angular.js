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

from datetime import UTC, datetime

from .store import Store

TP1_FRACTION = 0.5
TP2_FRACTION = 0.25


def _now() -> str:
    return datetime.now(UTC).isoformat()


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

    def update_with_candle(
        self, pos: dict, high: float, low: float, candle_ts: str | None = None
    ) -> dict:
        """Processa um candle contra uma posição aberta. Retorna a posição atualizada.

        `candle_ts` (ISO) é gravado como marcador `checked_until` nos eventos,
        permitindo ao chamador pular candles já processados (idempotência).
        """
        direction = pos["direction"]
        entry, stop = pos["entry"], pos["stop"]
        tp1, tp2, tp3 = pos["targets"][:3]
        events: list = [e for e in pos["events"] if e.get("type") != "checked_until"]
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
            past = {e.get("type") for e in events}
            stop_kind = "stop_breakeven" if "tp1" in past else "stop"
            events.append({"at": _now(), "type": stop_kind, "price": stop})
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

        if candle_ts is not None:
            events.append({"type": "checked_until", "ts": candle_ts})
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

    def update_with_tick(self, pos: dict, price: float) -> tuple[dict, bool]:
        """Avalia stop/alvos contra um preço ao vivo (tick).

        Fills no nível exato (stop no preço do stop, alvo no do alvo). Só
        persiste quando algum nível é cruzado — retorna (posição, mudou?).
        Um único tick pode cruzar níveis em sequência (tp1 e tp2, por ex.).
        """
        entry, stop = pos["entry"], pos["stop"]
        tp1, tp2, tp3 = pos["targets"][:3]
        events: list = list(pos["events"])
        remaining = pos["remaining_size"]
        realized = pos["realized_pnl"]
        is_long = pos["direction"] == "long"

        def hit_stop() -> bool:
            return price <= stop if is_long else price >= stop

        def hit(level: float) -> bool:
            return price >= level if is_long else price <= level

        def pnl(exit_price: float, size: float) -> float:
            return (exit_price - entry) * size if is_long else (entry - exit_price) * size

        hit_types = {e.get("type") for e in events}
        changed = False
        done = False

        if hit_stop():
            realized += pnl(stop, remaining)
            stop_kind = "stop_breakeven" if "tp1" in hit_types else "stop"
            events.append({"at": _now(), "type": stop_kind, "price": stop})
            remaining = 0.0
            changed = done = True
        else:
            if "tp1" not in hit_types and hit(tp1):
                part = min(pos["size"] * TP1_FRACTION, remaining)
                realized += pnl(tp1, part)
                remaining -= part
                stop = entry  # breakeven
                events.append({"at": _now(), "type": "tp1", "price": tp1})
                events.append({"at": _now(), "type": "breakeven", "price": entry})
                hit_types.add("tp1")
                changed = True
            if "tp2" not in hit_types and "tp1" in hit_types and hit(tp2):
                part = min(pos["size"] * TP2_FRACTION, remaining)
                realized += pnl(tp2, part)
                remaining -= part
                events.append({"at": _now(), "type": "tp2", "price": tp2})
                hit_types.add("tp2")
                changed = True
            if "tp2" in hit_types and hit(tp3):
                realized += pnl(tp3, remaining)
                remaining = 0.0
                events.append({"at": _now(), "type": "tp3", "price": tp3})
                changed = done = True

        if not changed:
            return pos, False

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
        return updated, True

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

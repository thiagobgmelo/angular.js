"""Gerenciador de execução: modos off/manual/auto, aprovações e circuit breaker.

- off    → sinais são apenas registrados (paper), nada vai à exchange
- manual → sinal vira aprovação pendente (validade limitada); ordem só sai
           após aprovar no Telegram ou dashboard
- auto   → executa direto, protegido pelo circuit breaker

O modo é persistido em settings (troca em runtime, sem restart). O circuit
breaker vale para os dois modos com ordem real: perda diária máxima, máximo
de entradas/dia e pausa automática após erros consecutivos.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

MODES = ("off", "manual", "auto")


def _now() -> datetime:
    return datetime.now(UTC)


class ExecutionManager:
    def __init__(self, cfg, store, executor, notify=None):
        """`notify(text, buttons)` — callback async p/ Telegram (opcional)."""
        self.cfg = cfg
        self.store = store
        self.executor = executor
        self.notify = notify
        self._error_streak = 0
        default_mode = cfg.get("execution.mode", "off")
        if self.mode not in MODES:
            self.store.set_setting("execution_mode", default_mode)

    # --- estado (persistido) ---
    @property
    def mode(self) -> str:
        return self.store.get_setting("execution_mode", self.cfg.get("execution.mode", "off"))

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Modo inválido: {mode!r} (use off|manual|auto)")
        self.store.set_setting("execution_mode", mode)
        log.info("modo de execução: %s", mode)

    @property
    def paused(self) -> bool:
        return self.store.get_setting("execution_paused", "0") == "1"

    def pause(self, reason: str = "manual") -> None:
        self.store.set_setting("execution_paused", "1")
        self.store.set_setting("execution_pause_reason", reason)
        log.warning("execução PAUSADA (%s)", reason)

    def resume(self) -> None:
        self.store.set_setting("execution_paused", "0")
        self.store.set_setting("execution_pause_reason", "")
        self._error_streak = 0
        log.info("execução retomada")

    # --- circuit breaker ---
    def breaker_status(self) -> dict:
        day_start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        equity_base = self.cfg.get("risk.account_equity", 10000.0)
        max_daily_loss = self.cfg.get("execution.max_daily_loss_pct", 0.03) * equity_base
        pnl_today = self.store.realized_pnl_since(day_start)
        entries_today = self.store.entries_since(day_start)
        max_trades = self.cfg.get("execution.max_trades_per_day", 6)
        tripped = []
        if pnl_today <= -max_daily_loss:
            tripped.append(f"perda diária {pnl_today:.2f} <= -{max_daily_loss:.2f}")
        if entries_today >= max_trades:
            tripped.append(f"{entries_today} entradas hoje (máx {max_trades})")
        max_errors = self.cfg.get("execution.max_consecutive_errors", 3)
        if self._error_streak >= max_errors:
            tripped.append(f"{self._error_streak} erros de ordem consecutivos")
        return {
            "pnl_today": round(pnl_today, 2),
            "max_daily_loss": round(max_daily_loss, 2),
            "entries_today": entries_today,
            "max_trades_per_day": max_trades,
            "error_streak": self._error_streak,
            "tripped": tripped,
        }

    # --- fluxo principal ---
    async def handle_signal(self, sd: dict, signal_id: int) -> dict:
        """Chamado pelo engine para todo sinal emitido."""
        mode = self.mode
        if mode == "off":
            return {"action": "off"}
        if self.paused:
            self.store.log_execution(
                _now().isoformat(), "skip", sd["symbol"],
                {"reason": "execução pausada"}, True,
            )
            return {"action": "skipped", "reason": "paused"}

        breaker = self.breaker_status()
        if breaker["tripped"]:
            self.pause("circuit breaker: " + "; ".join(breaker["tripped"]))
            if self.notify:
                await self.notify(
                    "⛔ <b>Circuit breaker acionado</b>\n" + "\n".join(breaker["tripped"])
                    + "\nExecução pausada — use /retomar após revisar.", None,
                )
            return {"action": "breaker", "tripped": breaker["tripped"]}

        if mode == "manual":
            ttl_min = self.cfg.get("execution.approval_ttl_min", 15)
            now = _now()
            approval_id = self.store.create_approval(
                signal_id, now.isoformat(), (now + timedelta(minutes=ttl_min)).isoformat()
            )
            if self.notify:
                lev = sd.get("suggested_leverage", 1)
                text = (
                    f"🔔 <b>Aprovar entrada?</b> (expira em {ttl_min} min)\n"
                    f"{'🟢 LONG' if sd['direction'] == 'long' else '🔴 SHORT'} "
                    f"{sd['symbol']} {sd['timeframe']} · {lev}x\n"
                    f"Entrada {sd['entry']:g} · Stop {sd['stop']:g} · "
                    f"Alvos {' / '.join(f'{t:g}' for t in sd['targets'])}"
                )
                buttons = [[
                    {"text": "✅ Aprovar", "callback_data": f"approve:{approval_id}"},
                    {"text": "❌ Rejeitar", "callback_data": f"reject:{approval_id}"},
                ]]
                await self.notify(text, buttons)
            log.info("aprovação #%s pendente para %s", approval_id, sd["symbol"])
            return {"action": "pending_approval", "approval_id": approval_id}

        # auto
        return await self._execute(sd)

    async def decide(self, approval_id: int, approve: bool, via: str) -> dict:
        """Decide uma aprovação pendente (Telegram/dashboard)."""
        self.store.expire_stale_approvals(_now().isoformat())
        approval = self.store.get_approval(approval_id)
        if approval is None:
            return {"ok": False, "error": "aprovação não encontrada"}
        status = "approved" if approve else "rejected"
        if not self.store.decide_approval(approval_id, status, _now().isoformat(), via):
            return {"ok": False, "error": f"não está mais pendente ({approval['status']})"}
        if not approve:
            log.info("aprovação #%s rejeitada via %s", approval_id, via)
            return {"ok": True, "status": "rejected"}
        sd = {
            "symbol": approval["symbol"], "direction": approval["direction"],
            "timeframe": approval["timeframe"], "entry": approval["entry"],
            "stop": approval["stop"], "targets": approval["targets"],
            "position_size": approval["position_size"],
            "suggested_leverage": approval["suggested_leverage"],
        }
        result = await self._execute(sd)
        return {"ok": result.get("ok", False), "status": "approved", "result": result}

    async def _execute(self, sd: dict) -> dict:
        result = await asyncio.to_thread(self.executor.execute_signal, sd)
        if result.get("ok"):
            self._error_streak = 0
        else:
            self._error_streak += 1
            max_errors = self.cfg.get("execution.max_consecutive_errors", 3)
            if self._error_streak >= max_errors:
                self.pause(f"{self._error_streak} erros de ordem consecutivos")
                if self.notify:
                    await self.notify(
                        "⛔ Execução pausada: erros de ordem consecutivos. "
                        "Verifique a exchange e use /retomar.", None,
                    )
        result["action"] = "executed" if result.get("ok") else "error"
        return result

    async def on_breakeven(self, symbol: str, price: float) -> None:
        """TP1 atingido na carteira paper → move o stop real para breakeven."""
        if self.mode == "off":
            return
        await asyncio.to_thread(self.executor.amend_stop, symbol, price)

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "paused": self.paused,
            "pause_reason": self.store.get_setting("execution_pause_reason", ""),
            "executor": self.executor.name,
            "breaker": self.breaker_status(),
            "pending_approvals": self.store.pending_approvals(_now().isoformat()),
        }

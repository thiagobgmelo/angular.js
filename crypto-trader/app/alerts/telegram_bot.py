"""Bot do Telegram bidirecional: comandos + botões de aprovação.

Segurança: só processa updates do TELEGRAM_CHAT_ID configurado — qualquer
outro chat é ignorado e logado. Long-polling via getUpdates (sem webhook,
funciona atrás de NAT/firewall).

Comandos: /status /posicoes /radar /modo off|manual|auto /pausar /retomar /ajuda
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import os

import httpx

log = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Comandos</b>\n"
    "/status — equity, modo de execução, universo, breaker\n"
    "/posicoes — posições abertas\n"
    "/radar — últimas oportunidades em formação\n"
    "/modo off|manual|auto — muda o modo de execução\n"
    "/pausar — pausa a execução (kill-switch)\n"
    "/retomar — retoma a execução\n"
    "/ajuda — esta mensagem"
)


class TelegramBot:
    def __init__(self, engine, execution):
        self.engine = engine
        self.execution = execution
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
        self._task: asyncio.Task | None = None
        self._offset = 0
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    # --- envio ---
    async def send(self, text: str, buttons: list | None = None) -> bool:
        if not self.configured:
            return False
        payload: dict = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if buttons:
            payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        try:
            resp = await self._http().post(f"{self._base()}/sendMessage", json=payload)
            return resp.status_code == 200
        except httpx.HTTPError as err:
            log.warning("telegram send falhou: %s", err)
            return False

    # --- ciclo de vida ---
    def start(self) -> None:
        if self.configured and self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._poll_loop())
            log.info("bot do Telegram ativo (long-polling)")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- internos ---
    def _base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=35)
        return self._client

    async def _poll_loop(self) -> None:
        while True:
            try:
                resp = await self._http().get(
                    f"{self._base()}/getUpdates",
                    params={"timeout": 25, "offset": self._offset},
                )
                for update in resp.json().get("result", []):
                    self._offset = update["update_id"] + 1
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.warning("telegram poll: %s — tentando de novo", err)
                await asyncio.sleep(5)

    async def _handle_update(self, update: dict) -> None:
        callback = update.get("callback_query")
        if callback:
            await self._handle_callback(callback)
            return
        message = update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if chat != self.chat_id:
            log.warning("telegram: mensagem de chat não autorizado %s ignorada", chat)
            return
        if text:
            await self._handle_command(text)

    async def _handle_callback(self, callback: dict) -> None:
        chat = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
        data = callback.get("data", "")
        if chat != self.chat_id:
            log.warning("telegram: callback de chat não autorizado %s ignorado", chat)
            return
        answer = ""
        if data.startswith(("approve:", "reject:")):
            action, _, raw_id = data.partition(":")
            result = await self.execution.decide(
                int(raw_id), approve=(action == "approve"), via="telegram"
            )
            if result.get("ok"):
                answer = "✅ Executada" if action == "approve" else "❌ Rejeitada"
            else:
                answer = f"⚠️ {result.get('error', 'falhou')}"
            await self.send(f"Aprovação #{raw_id}: {answer}")
        with contextlib.suppress(httpx.HTTPError):
            await self._http().post(
                f"{self._base()}/answerCallbackQuery",
                json={"callback_query_id": callback["id"], "text": answer[:190]},
            )

    async def _handle_command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip().lower()
        if cmd == "/status":
            await self.send(self._status_text())
        elif cmd == "/posicoes":
            await self.send(self._positions_text())
        elif cmd == "/radar":
            await self.send(self._radar_text())
        elif cmd == "/modo":
            if arg in ("off", "manual", "auto"):
                self.execution.set_mode(arg)
                await self.send(f"Modo de execução: <b>{arg}</b>")
            else:
                await self.send(
                    f"Modo atual: <b>{self.execution.mode}</b>\nUse /modo off|manual|auto"
                )
        elif cmd == "/pausar":
            self.execution.pause("comando /pausar")
            await self.send("⏸️ Execução pausada.")
        elif cmd == "/retomar":
            self.execution.resume()
            await self.send("▶️ Execução retomada.")
        else:
            await self.send(HELP_TEXT)

    def _status_text(self) -> str:
        s = self.engine.portfolio.summary()
        ex = self.execution.status()
        breaker = ex["breaker"]
        return (
            f"<b>Status</b>\n"
            f"Equity: {s['equity']:g} USDT · PnL {s['realized_pnl']:g}\n"
            f"Posições abertas: {s['open_positions']} · Trades: {s['closed_trades']}\n"
            f"Execução: <b>{ex['mode']}</b> ({ex['executor']})"
            + (" · ⏸️ PAUSADA" if ex["paused"] else "")
            + f"\nHoje: {breaker['entries_today']}/{breaker['max_trades_per_day']} entradas · "
            f"PnL {breaker['pnl_today']:g} (limite -{breaker['max_daily_loss']:g})\n"
            f"Universo: {len(self.engine.feed.pairs)} pares"
        )

    def _positions_text(self) -> str:
        positions = self.engine.portfolio.open_positions()
        if not positions:
            return "Sem posições abertas."
        lines = ["<b>Posições abertas</b>"]
        for p in positions:
            lines.append(
                f"{'🟢' if p['direction'] == 'long' else '🔴'} "
                f"{html.escape(p['symbol'])} {p['timeframe']} · "
                f"entrada {p['entry']:g} · stop {p['stop']:g} · resta {p['remaining_size']:g}"
            )
        return "\n".join(lines)

    def _radar_text(self) -> str:
        rows = self.engine.store.recent_radar(5)
        if not rows:
            return "Radar vazio por enquanto."
        lines = ["<b>Radar — últimas formações</b>"]
        for r in rows:
            promoted = " ✓" if r["promoted_signal_id"] else ""
            lines.append(
                f"{r['created_at'][11:16]} {r['direction'].upper()} "
                f"{html.escape(r['symbol'])} {r['timeframe']} "
                f"{r['score']}/{r['max_score']} — {html.escape(r['missing'])}{promoted}"
            )
        return "\n".join(lines)

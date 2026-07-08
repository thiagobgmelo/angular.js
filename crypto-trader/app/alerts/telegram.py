"""Alertas via Telegram Bot API. No-op silencioso se não configurado."""
from __future__ import annotations

import html
import os

import httpx


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str) -> bool:
    """Envia mensagem; retorna True em sucesso, False caso contrário."""
    if not is_configured():
        return False
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def format_signal(s: dict) -> str:
    arrow = "🟢 LONG" if s["direction"] == "long" else "🔴 SHORT"
    kind = "Swing trade" if s["trade_type"] == "swing" else "Day trade"
    targets = " / ".join(f"{t:g}" for t in s["targets"])
    symbol = html.escape(str(s["symbol"]))
    timeframe = html.escape(str(s["timeframe"]))
    lines = [
        f"<b>{arrow} — {symbol} ({timeframe})</b>",
        f"Tipo: {kind} | Confluência: {s['score']}/{s['max_score']}",
        f"Entrada: <code>{s['entry']:g}</code>",
        f"Stop: <code>{s['stop']:g}</code>",
        f"Alvos: <code>{targets}</code>",
        f"Posição sugerida: {s['position_size']:g} (risco {s['risk_amount']:g} USDT)",
    ]
    lev = s.get("suggested_leverage")
    if lev and lev > 1:
        lines += [
            f"Alavancagem sugerida: <b>{lev}x</b> "
            f"(margem ~{s.get('margin_required', 0):g} USDT, "
            f"liq. est. <code>{s.get('liquidation_price_est') or 0:g}</code>)",
            f"<i>{html.escape(str(s.get('leverage_rationale', '')))}</i>",
        ]
    lines += [
        "",
        "Racional:",
    ]
    lines += [f"• {html.escape(str(r))}" for r in s["rationale"]]
    return "\n".join(lines)

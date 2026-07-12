"""Executores de ordens: DryRun (simulado/log) e Bybit (perpétuos USDT).

Contrato comum:
- execute_signal(sd): abre a posição do sinal — define alavancagem (isolada),
  entrada a mercado, TP1 (50%) e TP2 (25%) como limits reduce-only e o stop
  como stop-market reduce-only do total
- amend_stop(symbol, price): move o stop (ex.: breakeven após TP1)
- check_connection(): valida credenciais/saldo sem enviar ordem

Segurança: chaves SÓ via env (BYBIT_API_KEY/SECRET); BYBIT_TESTNET=1 usa o
sandbox. Recomendação obrigatória para produção: chave sem permissão de
saque e com whitelist de IP.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

log = logging.getLogger(__name__)

TP1_FRACTION = 0.5
TP2_FRACTION = 0.25


def _now() -> str:
    return datetime.now(UTC).isoformat()


def to_swap_symbol(symbol: str) -> str:
    """BTC/USDT (spot) → BTC/USDT:USDT (perpétuo linear USDT no ccxt)."""
    return symbol if ":" in symbol else f"{symbol}:USDT"


class DryRunExecutor:
    """Simula a execução: registra cada ordem no log de auditoria, nada sai.

    Usado automaticamente quando não há chaves configuradas (e nos testes) —
    permite validar todo o fluxo de aprovação/circuit breaker sem risco.
    """

    name = "dry-run"

    def __init__(self, store):
        self.store = store
        self.calls: list[dict] = []  # inspecionável em testes

    def check_connection(self) -> dict:
        return {"ok": True, "mode": "dry-run", "detail": "nenhuma ordem será enviada"}

    def execute_signal(self, sd: dict) -> dict:
        symbol = to_swap_symbol(sd["symbol"])
        side = "buy" if sd["direction"] == "long" else "sell"
        qty = sd["position_size"]
        orders = {
            "leverage": sd.get("suggested_leverage", 1),
            "entry": {"type": "market", "side": side, "qty": qty},
            "tp1": {"type": "limit", "qty": qty * TP1_FRACTION, "price": sd["targets"][0],
                    "reduceOnly": True},
            "tp2": {"type": "limit", "qty": qty * TP2_FRACTION, "price": sd["targets"][1],
                    "reduceOnly": True},
            "stop": {"type": "stop-market", "qty": qty, "triggerPrice": sd["stop"],
                     "reduceOnly": True},
        }
        self.calls.append({"action": "execute_signal", "symbol": symbol, "orders": orders})
        self.store.log_execution(_now(), "entry", symbol, {"dry_run": True, **orders}, True)
        log.info("[dry-run] %s %s qty=%s lev=%sx", side.upper(), symbol, qty, orders["leverage"])
        return {"ok": True, "dry_run": True, "orders": orders}

    def amend_stop(self, symbol: str, price: float) -> dict:
        symbol = to_swap_symbol(symbol)
        self.calls.append({"action": "amend_stop", "symbol": symbol, "price": price})
        self.store.log_execution(
            _now(), "amend_stop", symbol, {"dry_run": True, "price": price}, True
        )
        return {"ok": True, "dry_run": True}


class BybitExecutor:
    """Execução real em perpétuos USDT da Bybit via ccxt.

    Validar SEMPRE primeiro na testnet (BYBIT_TESTNET=1). O amend de stop é
    feito por cancelamento + recriação (compatível com a API unificada).
    """

    name = "bybit"

    def __init__(self, store, api_key: str, api_secret: str, testnet: bool = True):
        import ccxt  # noqa: PLC0415

        self.store = store
        self.exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)
        self.testnet = testnet
        self._stop_order_ids: dict[str, str] = {}

    def check_connection(self) -> dict:
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get("USDT", {}).get("free")
            return {
                "ok": True,
                "mode": "testnet" if self.testnet else "PRODUÇÃO",
                "usdt_free": usdt,
            }
        except Exception as err:
            return {"ok": False, "error": str(err)}

    def execute_signal(self, sd: dict) -> dict:
        symbol = to_swap_symbol(sd["symbol"])
        side = "buy" if sd["direction"] == "long" else "sell"
        close_side = "sell" if side == "buy" else "buy"
        lev = int(sd.get("suggested_leverage", 1))
        try:
            self.exchange.load_markets()
            qty = float(self.exchange.amount_to_precision(symbol, sd["position_size"]))
            try:
                self.exchange.set_leverage(lev, symbol)
            except Exception as err:  # "leverage not modified" é benigno
                log.info("set_leverage %s: %s (ignorado se já configurada)", symbol, err)

            entry = self.exchange.create_order(symbol, "market", side, qty)
            tp1 = self.exchange.create_order(
                symbol, "limit", close_side, qty * TP1_FRACTION,
                sd["targets"][0], params={"reduceOnly": True},
            )
            tp2 = self.exchange.create_order(
                symbol, "limit", close_side, qty * TP2_FRACTION,
                sd["targets"][1], params={"reduceOnly": True},
            )
            stop = self.exchange.create_order(
                symbol, "market", close_side, qty, None,
                params={"triggerPrice": sd["stop"], "reduceOnly": True},
            )
            self._stop_order_ids[symbol] = stop.get("id", "")
            detail = {
                "leverage": lev, "qty": qty,
                "entry_id": entry.get("id"), "tp1_id": tp1.get("id"),
                "tp2_id": tp2.get("id"), "stop_id": stop.get("id"),
                "testnet": self.testnet,
            }
            self.store.log_execution(_now(), "entry", symbol, detail, True)
            log.info("BYBIT %s %s qty=%s lev=%sx (testnet=%s)",
                     side.upper(), symbol, qty, lev, self.testnet)
            return {"ok": True, **detail}
        except Exception as err:
            self.store.log_execution(_now(), "error", symbol, {"error": str(err)}, False)
            log.error("falha ao executar %s: %s", symbol, err)
            return {"ok": False, "error": str(err)}

    def amend_stop(self, symbol: str, price: float) -> dict:
        symbol = to_swap_symbol(symbol)
        try:
            old_id = self._stop_order_ids.get(symbol)
            if old_id:
                try:
                    self.exchange.cancel_order(old_id, symbol)
                except Exception as err:
                    log.warning("cancel stop antigo %s: %s", symbol, err)
            positions = self.exchange.fetch_positions([symbol])
            pos = next((p for p in positions if p.get("contracts")), None)
            if pos is None:
                return {"ok": False, "error": "sem posição aberta na exchange"}
            close_side = "sell" if pos["side"] == "long" else "buy"
            stop = self.exchange.create_order(
                symbol, "market", close_side, pos["contracts"], None,
                params={"triggerPrice": price, "reduceOnly": True},
            )
            self._stop_order_ids[symbol] = stop.get("id", "")
            self.store.log_execution(_now(), "amend_stop", symbol, {"price": price}, True)
            return {"ok": True, "stop_id": stop.get("id")}
        except Exception as err:
            self.store.log_execution(_now(), "error", symbol, {"error": str(err)}, False)
            return {"ok": False, "error": str(err)}


def make_executor(store):
    """Bybit quando há chaves no ambiente; senão dry-run (seguro por default)."""
    key = os.environ.get("BYBIT_API_KEY", "")
    secret = os.environ.get("BYBIT_API_SECRET", "")
    if key and secret:
        testnet = os.environ.get("BYBIT_TESTNET", "1") != "0"
        if not testnet:
            log.warning("BYBIT EM MODO PRODUÇÃO — ordens reais serão enviadas")
        return BybitExecutor(store, key, secret, testnet=testnet)
    return DryRunExecutor(store)

"""Screener de universo: seleciona as moedas monitoradas por critérios objetivos.

Critérios (todos configuráveis em `screener:` no config.yaml):
- mercado spot ativo com quote USDT
- liquidez: volume 24h em quote >= min_quote_volume_24h
- maturidade: histórico de candles 1d >= min_history_days (exclui listagens novas)
- exclusões estruturais: bases estáveis/fiat e tokens alavancados (UP/DOWN/BULL/BEAR)
- ranking por volume 24h, corte em max_pairs
- always_include: majors sempre presentes, imunes ao corte
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

STABLE_FIAT_BASES = {
    "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "PYUSD", "USDE", "UST",
    "EUR", "EURI", "GBP", "TRY", "BRL", "ARS", "JPY", "AUD", "XUSD", "AEUR",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")

DEFAULTS = {
    "enabled": True,
    "quote": "USDT",
    "min_quote_volume_24h": 20_000_000,
    "min_history_days": 180,
    "max_pairs": 25,
    "refresh_hours": 6,
    "always_include": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"],
    "exclude": [],
}


def _cfg(scfg: dict, key: str):
    return scfg.get(key, DEFAULTS[key])


def _is_leveraged(base: str) -> bool:
    return any(base.endswith(suf) for suf in LEVERAGED_SUFFIXES)


def select_universe(
    markets: dict[str, dict],
    tickers: dict[str, dict],
    history_ok: dict[str, bool],
    scfg: dict,
) -> list[dict]:
    """Filtragem pura (testável sem rede). Retorna [{symbol, volume_24h, pinned}]
    ordenado: pinned primeiro, depois por volume decrescente.

    `history_ok`: symbol -> tem histórico suficiente (avaliado fora, custa 1
    request por candidato). Símbolo ausente do dict = desconhecido = reprovado.
    """
    quote = _cfg(scfg, "quote")
    min_vol = _cfg(scfg, "min_quote_volume_24h")
    max_pairs = _cfg(scfg, "max_pairs")
    always = list(_cfg(scfg, "always_include"))
    exclude = set(_cfg(scfg, "exclude"))

    candidates: list[dict] = []
    for symbol, market in markets.items():
        if symbol in exclude or symbol in always:
            continue
        if not market.get("spot") or not market.get("active"):
            continue
        if market.get("quote") != quote:
            continue
        base = market.get("base", "")
        if base in STABLE_FIAT_BASES or _is_leveraged(base):
            continue
        ticker = tickers.get(symbol) or {}
        volume = ticker.get("quoteVolume") or 0
        if volume < min_vol:
            continue
        if not history_ok.get(symbol, False):
            continue
        candidates.append({"symbol": symbol, "volume_24h": float(volume), "pinned": False})

    candidates.sort(key=lambda c: c["volume_24h"], reverse=True)
    slots = max(max_pairs - len(always), 0)
    selected = candidates[:slots]

    pinned = [
        {
            "symbol": s,
            "volume_24h": float((tickers.get(s) or {}).get("quoteVolume") or 0),
            "pinned": True,
        }
        for s in always
        if s not in exclude
    ]
    return pinned + selected


class Screener:
    """Screener sobre uma exchange real (REST síncrono, roda em thread)."""

    def __init__(self, exchange, scfg: dict):
        self.exchange = exchange  # instância ccxt síncrona
        self.scfg = scfg

    def screen(self) -> list[dict]:
        markets = self.exchange.load_markets()
        tickers = self.exchange.fetch_tickers()
        candidates = self._precandidates(markets, tickers)
        history_ok = self._check_history(candidates)
        universe = select_universe(markets, tickers, history_ok, self.scfg)
        log.info(
            "screener: %d pares selecionados: %s",
            len(universe),
            ", ".join(f"{u['symbol']}({u['volume_24h'] / 1e6:.0f}M)" for u in universe),
        )
        return universe

    def _precandidates(self, markets: dict, tickers: dict) -> list[str]:
        """Candidatos que passam nos filtros baratos — só estes pagam a
        checagem de histórico (1 request cada)."""
        quote = _cfg(self.scfg, "quote")
        min_vol = _cfg(self.scfg, "min_quote_volume_24h")
        always = set(_cfg(self.scfg, "always_include"))
        exclude = set(_cfg(self.scfg, "exclude"))
        out = []
        for symbol, market in markets.items():
            if symbol in always or symbol in exclude:
                continue
            if not market.get("spot") or not market.get("active"):
                continue
            if market.get("quote") != quote:
                continue
            base = market.get("base", "")
            if base in STABLE_FIAT_BASES or _is_leveraged(base):
                continue
            if ((tickers.get(symbol) or {}).get("quoteVolume") or 0) < min_vol:
                continue
            out.append(symbol)
        # limita o custo da checagem de histórico aos maiores por volume
        out.sort(key=lambda s: tickers[s].get("quoteVolume") or 0, reverse=True)
        return out[: _cfg(self.scfg, "max_pairs") * 3]

    def _check_history(self, symbols: list[str]) -> dict[str, bool]:
        min_days = _cfg(self.scfg, "min_history_days")
        since = int((time.time() - min_days * 86400) * 1000)
        ok: dict[str, bool] = {}
        for symbol in symbols:
            try:
                candles = self.exchange.fetch_ohlcv(symbol, "1d", since=since, limit=2)
                # existir candle perto do início da janela = já era listada
                ok[symbol] = bool(candles) and candles[0][0] <= since + 7 * 86400 * 1000
            except Exception as err:
                log.warning("screener: histórico de %s indisponível (%s)", symbol, err)
                ok[symbol] = False
        return ok


class DemoScreener:
    """Screener offline: seleciona sobre os pares sintéticos do DemoClient."""

    def __init__(self, scfg: dict):
        self.scfg = scfg

    def screen(self) -> list[dict]:
        from app.data.exchange import DemoClient  # noqa: PLC0415

        quote = _cfg(self.scfg, "quote")
        markets, tickers, history = {}, {}, {}
        for i, (symbol, base_price) in enumerate(DemoClient.BASE_PRICES.items()):
            base = symbol.split("/")[0]
            markets[symbol] = {"spot": True, "active": True, "quote": quote, "base": base}
            # volumes sintéticos decrescentes e determinísticos, todos acima do
            # limiar default — o corte fica por conta de max_pairs
            tickers[symbol] = {"quoteVolume": 900_000_000 / (i + 1) + base_price}
            history[symbol] = True
        universe = select_universe(markets, tickers, history, self.scfg)
        log.info("screener demo: %d pares", len(universe))
        return universe


def make_screener(cfg) -> Screener | DemoScreener:
    import os  # noqa: PLC0415

    scfg = cfg.get("screener", {}) or {}
    exchange_id = os.environ.get("EXCHANGE_ID", cfg.get("exchange.id", "binance"))
    if exchange_id == "demo":
        return DemoScreener(scfg)
    import ccxt  # noqa: PLC0415

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    return Screener(exchange, scfg)

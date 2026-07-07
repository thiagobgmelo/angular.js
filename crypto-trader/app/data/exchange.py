"""Wrapper ccxt para busca de mercado (OHLCV público, sem API key).

Inclui um provedor `demo` com dados sintéticos determinísticos para uso
offline (testes, demonstração do dashboard, ambientes sem acesso à exchange).
"""
from __future__ import annotations

import logging
import os
import time
import zlib

import ccxt
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def make_client(exchange_id: str = "binance", rate_limit: bool = True):
    """Fábrica: EXCHANGE_ID no ambiente sobrepõe o config; `demo` é offline."""
    exchange_id = os.environ.get("EXCHANGE_ID", exchange_id)
    if exchange_id == "demo":
        return DemoClient()
    return ExchangeClient(exchange_id, rate_limit)


class ExchangeClient:
    def __init__(self, exchange_id: str = "binance", rate_limit: bool = True):
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({"enableRateLimit": rate_limit})
        self.id = exchange_id

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 400,
        since: int | None = None,
        retries: int = 3,
    ) -> pd.DataFrame:
        """Busca candles e retorna DataFrame indexado por datetime UTC."""
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                raw = self.exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe, since=since, limit=limit
                )
                break
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as err:
                last_err = err
                log.warning(
                    "%s %s: falha de rede (tentativa %d/%d): %s",
                    symbol, timeframe, attempt + 1, retries, err,
                )
                time.sleep(2**attempt)
        else:
            raise ConnectionError(
                f"Falha ao buscar OHLCV de {symbol} em {self.id}: {last_err}"
            )

        df = pd.DataFrame(raw, columns=OHLCV_COLUMNS)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        return df.astype(float)

    def fetch_ohlcv_history(
        self, symbol: str, timeframe: str, total: int = 2000
    ) -> pd.DataFrame:
        """Pagina para trás até acumular `total` candles (para backtest)."""
        tf_ms = self.exchange.parse_timeframe(timeframe) * 1000
        since = self.exchange.milliseconds() - total * tf_ms
        frames: list[pd.DataFrame] = []
        fetched = 0
        while fetched < total:
            df = self.fetch_ohlcv(symbol, timeframe, limit=min(1000, total - fetched), since=since)
            if df.empty:
                break
            frames.append(df)
            fetched += len(df)
            since = int(df.index[-1].timestamp() * 1000) + tf_ms
            if len(df) < 2:
                break
        if not frames:
            return pd.DataFrame(columns=OHLCV_COLUMNS[1:])
        out = pd.concat(frames)
        return out[~out.index.duplicated(keep="first")].sort_index()

    def last_price(self, symbol: str) -> float:
        return float(self.exchange.fetch_ticker(symbol)["last"])


class DemoClient:
    """Gera OHLCV sintético determinístico (passeio aleatório com regimes).

    Mesma interface pública do ExchangeClient; candles alinhados ao relógio,
    então o último candle "fecha" de verdade a cada período.
    """

    id = "demo"

    BASE_PRICES = {
        "BTC/USDT": 60000.0,
        "ETH/USDT": 3000.0,
        "SOL/USDT": 150.0,
        "BNB/USDT": 600.0,
    }
    TF_SECONDS = {
        "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "4h": 14400, "1d": 86400,
    }

    def _series(self, symbol: str, timeframe: str, n: int, end_ts: int) -> pd.DataFrame:
        seed = zlib.crc32(f"{symbol}|{timeframe}".encode())  # estável entre processos
        rng = np.random.default_rng(seed)
        base = self.BASE_PRICES.get(symbol, 100.0)
        # série longa de tamanho fixo: pedidos com `limit` diferentes recebem
        # o mesmo final de série (consistência entre gráfico, análise e backtest)
        total = max(5000, n)
        # regimes de tendência alternados + ruído + ciclo
        choices = rng.choice([-1, 0, 1], size=total // 40 + 1, p=[0.3, 0.2, 0.5])
        regime = np.repeat(choices, 40)[:total]
        drift = regime * 0.0015
        noise = rng.normal(0, 0.012, total)
        cycle = 0.02 * np.sin(np.arange(total) / 12.0)
        log_ret = drift + noise + np.diff(np.concatenate([[0], cycle]))
        closes = base * np.exp(np.cumsum(log_ret))
        opens = np.concatenate([[closes[0]], closes[:-1]])
        span = np.abs(rng.normal(0, 0.006, total)) * closes
        highs = np.maximum(opens, closes) + span
        lows = np.minimum(opens, closes) - span
        vols = rng.lognormal(4, 0.5, total) * (1 + np.abs(log_ret) * 40)

        tf = self.TF_SECONDS.get(timeframe, 14400)
        last_close_ts = (end_ts // tf) * tf
        ts = np.arange(total) * tf + (last_close_ts - (total - 1) * tf)
        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
            index=pd.to_datetime(ts, unit="s", utc=True),
        )
        return df.tail(n)

    def fetch_ohlcv(self, symbol: str, timeframe: str = "4h", limit: int = 400,
                    since: int | None = None, retries: int = 3) -> pd.DataFrame:
        return self._series(symbol, timeframe, limit, int(time.time()))

    def fetch_ohlcv_history(self, symbol: str, timeframe: str, total: int = 2000) -> pd.DataFrame:
        return self._series(symbol, timeframe, total, int(time.time()))

    def last_price(self, symbol: str) -> float:
        return float(self.fetch_ohlcv(symbol, "1m", limit=2)["close"].iloc[-1])

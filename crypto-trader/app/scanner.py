"""Scanner: percorre pares × timeframes, emite sinais e atualiza a carteira paper."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.alerts import telegram
from app.analysis import strategy
from app.config import Config, db_path
from app.data.exchange import make_client
from app.paper.portfolio import PaperPortfolio
from app.paper.store import Store
from app.risk.manager import RiskManager

log = logging.getLogger(__name__)

# não repete o mesmo sinal (par+timeframe+direção) dentro desta janela
DEDUP_HOURS = {"15m": 2, "1h": 8, "4h": 24, "1d": 72}


class Scanner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = make_client(
            cfg.get("exchange.id", "binance"), cfg.get("exchange.rate_limit", True)
        )
        self.store = Store(db_path(cfg))
        self.portfolio = PaperPortfolio(
            self.store, cfg.get("risk.account_equity", 10000.0)
        )
        self.risk = RiskManager(
            risk_per_trade=cfg.get("risk.risk_per_trade", 0.01),
            min_risk_reward=cfg.get("strategy.min_risk_reward", 1.5),
            max_open_positions=cfg.get("risk.max_open_positions", 5),
        )

    def scan_once(self, verbose: bool = True) -> list[dict]:
        """Uma passada completa. Retorna os sinais novos emitidos (como dict)."""
        pairs = self.cfg.get("market.pairs", ["BTC/USDT"])
        timeframes = self.cfg.get("market.timeframes", ["4h"])
        candles = self.cfg.get("market.candles", 400)
        scfg = self.cfg.get("strategy", {})
        new_signals: list[dict] = []

        self._update_open_positions()

        for symbol in pairs:
            for timeframe in timeframes:
                try:
                    df = self.client.fetch_ohlcv(symbol, timeframe, limit=candles)
                except Exception as err:  # rede/exchange: segue para o próximo
                    log.warning("%s %s: erro ao buscar dados (%s)", symbol, timeframe, err)
                    continue
                if len(df) < 60:
                    continue
                # analisa apenas candles fechados
                signal, context = strategy.analyze(df.iloc[:-1], symbol, timeframe, scfg)
                if verbose:
                    print(
                        f"  {symbol:>10} {timeframe:>4} | preço {context['price']:>12g} | "
                        f"{context['structure']:>9} | RSI {context['rsi']:>5} | "
                        f"L{context['long_score']}/S{context['short_score']}"
                    )
                if signal is None:
                    continue

                dedup_h = DEDUP_HOURS.get(timeframe, 24)
                since = (datetime.now(UTC) - timedelta(hours=dedup_h)).isoformat()
                if self.store.has_recent_signal(symbol, timeframe, signal.direction, since):
                    continue

                open_count = len(self.portfolio.open_positions())
                sized = self.risk.apply(signal, self.portfolio.equity, open_count)
                if sized is None:
                    continue

                sd = sized.to_dict()
                signal_id = self.store.save_signal(sd)
                self.portfolio.open_from_signal(signal_id, sd)
                new_signals.append(sd)
                if verbose:
                    print(f"  >>> SINAL {sd['direction'].upper()} {symbol} {timeframe} "
                          f"entrada {sd['entry']:g} stop {sd['stop']:g} alvos {sd['targets']}")
                if telegram.is_configured():
                    telegram.send(telegram.format_signal(sd))

        return new_signals

    def _update_open_positions(self) -> None:
        """Confere TP/SL das posições abertas com os candles fechados ainda não vistos.

        Idempotente: só processa candles com timestamp posterior ao marcador
        `checked_until` da posição (ou à abertura, na primeira passada).
        """
        for pos in self.portfolio.open_positions():
            checked_until = max(
                (e.get("ts", "") for e in pos["events"] if e.get("type") == "checked_until"),
                default=pos["opened_at"],
            )
            try:
                df = self.client.fetch_ohlcv(pos["symbol"], pos["timeframe"], limit=50)
            except Exception as err:
                log.warning("posição %s: erro ao buscar candles (%s)", pos["id"], err)
                continue
            for ts, candle in df.iloc[:-1].iterrows():  # apenas candles fechados
                ts_iso = ts.isoformat()
                if ts_iso <= checked_until:
                    continue
                pos = self.portfolio.update_with_candle(
                    pos, float(candle["high"]), float(candle["low"]), candle_ts=ts_iso
                )
                if pos["status"] == "closed":
                    break

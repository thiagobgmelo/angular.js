"""CLI do sistema de trading.

Uso:
  python -m app.main scan                  # uma passada de análise em todos os pares
  python -m app.main watch                 # scanner contínuo (intervalo do config)
  python -m app.main analyze BTC/USDT 4h   # análise detalhada de um par
  python -m app.main backtest BTC/USDT 4h [candles]
  python -m app.main serve [porta]         # API + dashboard
"""
from __future__ import annotations

import json
import logging
import sys

from app.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def cmd_scan() -> None:
    from app.scanner import Scanner

    scanner = Scanner(load_config())
    print(f"Escaneando {scanner.client.id}...")
    signals = scanner.scan_once()
    print(f"\n{len(signals)} sinal(is) novo(s).")
    summary = scanner.portfolio.summary()
    print(f"Carteira paper: equity {summary['equity']} | "
          f"posições abertas {summary['open_positions']}")


def cmd_watch() -> None:
    import asyncio

    from app.engine import run_live

    print("Engine em tempo real: análise em candle fechado + stops tick a tick")
    try:
        asyncio.run(run_live(load_config()))
    except KeyboardInterrupt:
        print("\nEncerrado.")


def cmd_analyze(symbol: str, timeframe: str) -> None:
    from app.analysis import strategy
    from app.data.exchange import make_client

    cfg = load_config()
    client = make_client(cfg.get("exchange.id", "binance"))
    df = client.fetch_ohlcv(symbol, timeframe, limit=cfg.get("market.candles", 400))
    signal, context = strategy.analyze(df.iloc[:-1], symbol, timeframe, cfg.get("strategy", {}))

    print(f"\n=== {symbol} {timeframe} — preço {context['price']:g} ===")
    print(f"Estrutura: {context['structure']} | RSI {context['rsi']} | "
          f"Suporte {context['support']} | Resistência {context['resistance']}")
    print(f"Score: LONG {context['long_score']}/{context['max_score']} | "
          f"SHORT {context['short_score']}/{context['max_score']}")
    for side, why in (("LONG", context["long_rationale"]), ("SHORT", context["short_rationale"])):
        if why:
            print(f"\nCritérios {side}:")
            for r in why:
                print(f"  • {r}")
    if signal:
        print(f"\n>>> SINAL {signal.direction.upper()} ({signal.trade_type})")
        alvos = [f"{t:g}" for t in signal.targets]
        rr = signal.rr_to(signal.targets[0])
        print(f"    Entrada {signal.entry:g} | Stop {signal.stop:g} | "
              f"Alvos {alvos} | R:R(TP1) {rr:.2f}")
    else:
        print("\nSem sinal no momento (confluência insuficiente).")


def cmd_backtest(symbol: str, timeframe: str, candles: int = 1500) -> None:
    from app.backtest import engine
    from app.data.exchange import make_client

    cfg = load_config()
    client = make_client(cfg.get("exchange.id", "binance"))
    print(f"Buscando ~{candles} candles de {symbol} {timeframe}...")
    df = client.fetch_ohlcv_history(symbol, timeframe, total=candles)
    print(f"{len(df)} candles ({df.index[0]} → {df.index[-1]})")
    result = engine.run(
        df, symbol, timeframe, cfg.get("strategy", {}),
        initial_equity=cfg.get("risk.account_equity", 10000.0),
        risk_per_trade=cfg.get("risk.risk_per_trade", 0.01),
    )
    result.pop("equity_curve")
    trade_list = result.pop("trade_list")
    print(json.dumps(result, indent=2))
    if trade_list:
        print("\nÚltimos trades:")
        for t in trade_list[-10:]:
            print(f"  {t['opened_at']} {t['direction']:>5} @ {t['entry']:g} → "
                  f"{t['exit_reason']:<14} pnl {t['pnl']:>10}")


def cmd_serve(port: int = 8000) -> None:
    import os

    import uvicorn

    # localhost por padrão; exporte HOST=0.0.0.0 conscientemente para expor
    # na rede (a API tem endpoint mutável e não tem autenticação)
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run("app.server:app", host=host, port=port)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    cmd, rest = args[0], args[1:]
    if cmd == "scan":
        cmd_scan()
    elif cmd == "watch":
        cmd_watch()
    elif cmd == "analyze":
        cmd_analyze(rest[0] if rest else "BTC/USDT", rest[1] if len(rest) > 1 else "4h")
    elif cmd == "backtest":
        cmd_backtest(
            rest[0] if rest else "BTC/USDT",
            rest[1] if len(rest) > 1 else "4h",
            int(rest[2]) if len(rest) > 2 else 1500,
        )
    elif cmd == "serve":
        cmd_serve(int(rest[0]) if rest else 8000)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

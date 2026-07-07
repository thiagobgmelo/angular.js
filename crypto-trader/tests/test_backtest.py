import numpy as np

from app.backtest import engine
from tests.conftest import make_ohlcv

CFG = {
    "score_threshold": 3,
    "min_risk_reward": 1.5,
    "swing_lookback": 5,
    "sr_zone_tolerance": 0.005,
}


def trending_series(n=800, seed=7):
    """Série com regimes de tendência — gera trades no backtest."""
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-1, 1], size=n // 50 + 1), 50)[:n]
    log_ret = regime * 0.004 + rng.normal(0, 0.01, n)
    closes = 100 * np.exp(np.cumsum(log_ret))
    return make_ohlcv(closes, freq="4h", spread=0.008)


def test_backtest_runs_and_accounts_correctly():
    df = trending_series()
    result = engine.run(df, "TEST/USDT", "4h", CFG, initial_equity=10000, risk_per_trade=0.01)
    assert result["trades"] == result["wins"] + result["losses"]
    total_pnl = sum(t["pnl"] for t in result["trade_list"])
    # equity final = inicial + soma dos pnls (contabilidade fecha)
    assert abs(result["final_equity"] - (10000 + total_pnl)) < 0.02
    assert 0 <= result["max_drawdown_pct"] <= 100


def test_backtest_loss_bounded_by_risk():
    """Nenhum trade perde muito mais que o risco configurado (1% + arredondamento)."""
    df = trending_series(seed=11)
    result = engine.run(df, "TEST/USDT", "4h", CFG, initial_equity=10000, risk_per_trade=0.01)
    for t in result["trade_list"]:
        if t["exit_reason"] == "stop":
            assert t["pnl"] >= -10000 * 0.01 * 1.05  # tolerância de 5%


def test_backtest_no_trades_on_flat_series():
    closes = np.full(600, 100.0) + np.sin(np.arange(600) / 30.0) * 0.2
    df = make_ohlcv(closes, freq="4h", spread=0.001)
    result = engine.run(df, "TEST/USDT", "4h", dict(CFG, score_threshold=6))
    assert result["trades"] == 0
    assert result["final_equity"] == result["initial_equity"]


def test_backtest_metrics_shape():
    df = trending_series(seed=3)
    result = engine.run(df, "TEST/USDT", "4h", CFG)
    for key in ("symbol", "timeframe", "trades", "win_rate", "profit_factor",
                "return_pct", "max_drawdown_pct", "equity_curve", "trade_list"):
        assert key in result
    if result["trades"]:
        assert all(t["exit_reason"] is not None for t in result["trade_list"])

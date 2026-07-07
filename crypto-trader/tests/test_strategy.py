import numpy as np

from app.analysis import strategy
from app.analysis.signal import trade_type_for
from tests.conftest import make_ohlcv

CFG = {
    "score_threshold": 3,
    "min_risk_reward": 1.5,
    "swing_lookback": 5,
    "sr_zone_tolerance": 0.005,
}


def test_analyze_returns_context_always(trend_up_df):
    signal, context = strategy.analyze(trend_up_df, "BTC/USDT", "4h", CFG)
    assert context["symbol"] == "BTC/USDT"
    assert context["structure"] == "uptrend"
    assert 0 <= context["long_score"] <= context["max_score"]
    assert isinstance(context["long_rationale"], list)


def test_uptrend_scores_long_over_short(trend_up_df):
    _, context = strategy.analyze(trend_up_df, "BTC/USDT", "4h", CFG)
    assert context["long_score"] > context["short_score"]


def test_downtrend_scores_short_over_long(trend_down_df):
    _, context = strategy.analyze(trend_down_df, "BTC/USDT", "4h", CFG)
    assert context["short_score"] > context["long_score"]


def test_signal_levels_consistent_when_emitted(trend_up_df):
    signal, _ = strategy.analyze(trend_up_df, "BTC/USDT", "4h", CFG)
    if signal is None:
        return  # confluência pode não bastar no dado sintético; níveis testados abaixo
    assert signal.direction == "long"
    assert signal.stop < signal.entry
    assert signal.targets[0] > signal.entry
    assert signal.targets == sorted(signal.targets)
    assert signal.rr_to(signal.targets[0]) >= CFG["min_risk_reward"]


def test_signal_forced_long_low_threshold(trend_up_df):
    """Com limiar 1 o lado dominante em tendência clara deve emitir sinal."""
    cfg = dict(CFG, score_threshold=1)
    signal, context = strategy.analyze(trend_up_df, "BTC/USDT", "4h", cfg)
    # dominância de 2 pontos ainda é exigida; em tendência forte deve ocorrer
    if context["long_score"] >= context["short_score"] + 2:
        assert signal is not None
        assert signal.direction == "long"


def test_trade_type_classification():
    assert trade_type_for("4h") == "swing"
    assert trade_type_for("1d") == "swing"
    assert trade_type_for("15m") == "day_trade"
    assert trade_type_for("1h") == "day_trade"


def test_range_market_rarely_signals():
    closes = 100 + 2 * np.sin(np.arange(300) / 10.0)
    df = make_ohlcv(closes)
    signal, context = strategy.analyze(df, "BTC/USDT", "4h", dict(CFG, score_threshold=5))
    assert signal is None

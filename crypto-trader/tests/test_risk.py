from app.analysis.signal import Signal
from app.risk.manager import RiskManager


def make_signal(entry=100.0, stop=95.0, targets=None, direction="long"):
    return Signal(
        symbol="BTC/USDT",
        timeframe="4h",
        direction=direction,
        trade_type="swing",
        entry=entry,
        stop=stop,
        targets=targets or [107.5, 112.5, 120.0],
        score=5,
        max_score=7,
    )


def test_position_size_risks_exactly_one_percent():
    rm = RiskManager(risk_per_trade=0.01)
    size = rm.position_size(equity=10000, entry=100, stop=95)
    assert abs(size * (100 - 95) - 100) < 1e-9  # perde exatamente 100 no stop


def test_apply_fills_size_and_risk():
    rm = RiskManager(risk_per_trade=0.01)
    s = rm.apply(make_signal(), equity=10000)
    assert s is not None
    assert s.position_size > 0
    assert s.risk_amount == 100.0


def test_apply_rejects_bad_risk_reward():
    rm = RiskManager(min_risk_reward=1.5)
    s = make_signal(entry=100, stop=95, targets=[103, 110, 120])  # R:R TP1 = 0.6
    assert rm.apply(s, equity=10000) is None


def test_apply_rejects_when_max_positions_reached():
    rm = RiskManager(max_open_positions=2)
    assert rm.apply(make_signal(), equity=10000, open_positions=2) is None


def test_zero_risk_distance_rejected():
    rm = RiskManager()
    s = make_signal(entry=100, stop=100)
    assert rm.apply(s, equity=10000) is None

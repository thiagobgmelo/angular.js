from app.risk.manager import MAINTENANCE_MARGIN_RATE, RiskManager
from tests.test_risk import make_signal


def liq_distance(lev: int) -> float:
    return 1.0 / lev - MAINTENANCE_MARGIN_RATE


def test_liquidation_buffer_respected():
    rm = RiskManager(max_leverage=10, liq_buffer=3.0)
    entry, stop = 100.0, 97.0  # stop a 3%
    lev, liq_price, why = rm.suggest_leverage(entry, stop)
    stop_dist = 0.03
    assert liq_distance(lev) >= 3.0 * stop_dist - 1e-9
    assert liq_price < stop  # long: liquidação bem abaixo do stop
    assert "3" in why or "Teto" in why


def test_cap_applied_for_tight_stop():
    rm = RiskManager(max_leverage=10, liq_buffer=3.0)
    lev, _, why = rm.suggest_leverage(100.0, 99.5)  # stop a 0,5% → regra daria ~50x
    assert lev == 10
    assert "Teto" in why


def test_wide_stop_gives_low_leverage_min_one():
    rm = RiskManager(max_leverage=10, liq_buffer=3.0)
    lev, _, _ = rm.suggest_leverage(100.0, 80.0)  # stop a 20%
    assert lev == 1


def test_tighter_stop_never_lowers_leverage():
    rm = RiskManager(max_leverage=10, liq_buffer=3.0)
    lev_tight, _, _ = rm.suggest_leverage(100.0, 98.0)
    lev_wide, _, _ = rm.suggest_leverage(100.0, 94.0)
    assert lev_tight >= lev_wide


def test_short_liquidation_above_stop():
    rm = RiskManager(max_leverage=10, liq_buffer=3.0)
    lev, liq_price, _ = rm.suggest_leverage(100.0, 103.0)  # short: stop acima
    assert liq_price > 103.0


def test_apply_fills_leverage_fields():
    rm = RiskManager(risk_per_trade=0.01, max_leverage=10, liq_buffer=3.0)
    s = rm.apply(make_signal(entry=100.0, stop=97.0), equity=10000)
    assert s is not None
    assert 1 <= s.suggested_leverage <= 10
    notional = s.position_size * s.entry
    assert abs(s.margin_required - notional / s.suggested_leverage) < 0.02
    assert s.leverage_rationale
    assert s.liquidation_price_est is not None

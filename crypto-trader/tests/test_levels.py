import numpy as np

from app.analysis import levels
from tests.conftest import make_ohlcv


def test_swing_points_detects_peak_and_trough():
    closes = [100, 101, 102, 103, 110, 103, 102, 101, 100,
              99, 95, 99, 100, 101, 102, 103, 104, 105, 106]
    df = make_ohlcv(closes, spread=0.0)
    highs, lows = levels.swing_points(df, lookback=3)
    assert any(abs(v - 110 * 1.0) < 1e-6 or v >= 109 for v in highs.values)
    assert any(v <= 96 for v in lows.values)


def test_market_structure_uptrend(trend_up_df):
    assert levels.market_structure(trend_up_df) == "uptrend"


def test_market_structure_downtrend(trend_down_df):
    assert levels.market_structure(trend_down_df) == "downtrend"


def test_sr_zones_and_nearest(trend_up_df):
    zones = levels.sr_zones(trend_up_df)
    assert zones
    price = float(trend_up_df["close"].iloc[-1])
    support, resistance = levels.nearest_zones(zones, price)
    if support:
        assert support.price < price
    if resistance:
        assert resistance.price > price


def test_fibonacci_levels_ordered(trend_up_df):
    fib = levels.fibonacci_retracement(trend_up_df)
    assert fib, "deve encontrar uma perna com os swings do zigue-zague"
    lo, hi = min(fib["0.0"], fib["1.0"]), max(fib["0.0"], fib["1.0"])
    for key in ("0.382", "0.500", "0.618"):
        assert lo <= fib[key] <= hi


def test_in_zone_tolerance():
    zone = levels.Zone(price=100.0, touches=2, kind="support")
    assert levels.in_zone(100.4, zone, tolerance=0.005)
    assert not levels.in_zone(102.0, zone, tolerance=0.005)

import numpy as np
import pandas as pd

from app.analysis import indicators
from tests.conftest import make_ohlcv


def test_sma_known_values():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = indicators.sma(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == 2.0
    assert out.iloc[4] == 4.0


def test_ema_converges_to_constant():
    s = pd.Series([10.0] * 100)
    out = indicators.ema(s, 20)
    assert abs(out.iloc[-1] - 10.0) < 1e-9


def test_rsi_bounds_and_direction():
    up = pd.Series(np.linspace(1, 100, 60))
    down = pd.Series(np.linspace(100, 1, 60))
    rsi_up = indicators.rsi(up).iloc[-1]
    rsi_down = indicators.rsi(down).iloc[-1]
    assert 0 <= rsi_down <= 100 and 0 <= rsi_up <= 100
    assert rsi_up > 95          # alta contínua → RSI ~100
    assert rsi_down < 5         # queda contínua → RSI ~0


def test_rsi_flat_series_no_nan_after_warmup():
    s = pd.Series([50.0] * 40)
    out = indicators.rsi(s, 14)
    assert not out.iloc[20:].isna().any()


def test_macd_positive_in_uptrend():
    s = pd.Series(np.linspace(10, 100, 120))
    out = indicators.macd(s)
    assert out["macd"].iloc[-1] > 0
    assert set(out.columns) == {"macd", "signal", "histogram"}


def test_atr_positive_and_scales_with_range():
    df_small = make_ohlcv(np.linspace(100, 110, 60), spread=0.001)
    df_big = make_ohlcv(np.linspace(100, 110, 60), spread=0.05)
    atr_small = indicators.atr(df_small).iloc[-1]
    atr_big = indicators.atr(df_big).iloc[-1]
    assert atr_small > 0
    assert atr_big > atr_small * 5


def test_bollinger_ordering():
    df = make_ohlcv(100 + 5 * np.sin(np.arange(80) / 4.0))
    bb = indicators.bollinger(df["close"])
    last = bb.dropna().iloc[-1]
    assert last["bb_lower"] < last["bb_mid"] < last["bb_upper"]


def test_enrich_adds_all_columns():
    df = make_ohlcv(np.linspace(100, 150, 260))
    out = indicators.enrich(df)
    for col in ("ema_fast", "ema_slow", "rsi", "macd", "signal", "histogram",
                "atr", "bb_upper", "bb_mid", "bb_lower", "volume_ma"):
        assert col in out.columns
    assert not out.iloc[-1][["ema_fast", "ema_slow", "rsi", "atr"]].isna().any()

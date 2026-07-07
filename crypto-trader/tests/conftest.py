import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_ohlcv(closes, start="2024-01-01", freq="4h", spread=0.01, volume=100.0):
    """Constrói um DataFrame OHLCV sintético a partir de uma lista de fechamentos."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    vols = np.full(len(closes), float(volume))
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=idx,
    )


@pytest.fixture
def trend_up_df():
    """300 candles em tendência de alta com zigue-zague (swings detectáveis)."""
    base = np.linspace(100, 200, 300)
    wave = 4 * np.sin(np.arange(300) / 6.0)
    return make_ohlcv(base + wave)


@pytest.fixture
def trend_down_df():
    base = np.linspace(200, 100, 300)
    wave = 4 * np.sin(np.arange(300) / 6.0)
    return make_ohlcv(base + wave)

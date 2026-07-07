import pytest

from app.paper.portfolio import PaperPortfolio
from app.paper.store import Store


@pytest.fixture
def portfolio(tmp_path):
    store = Store(tmp_path / "test.db")
    return PaperPortfolio(store, initial_equity=10000.0)


def long_signal_dict(entry=100.0, stop=95.0, targets=(107.5, 112.5, 120.0), size=20.0):
    return {
        "created_at": "2024-01-01T00:00:00+00:00",
        "symbol": "BTC/USDT",
        "timeframe": "4h",
        "direction": "long",
        "trade_type": "swing",
        "entry": entry,
        "stop": stop,
        "targets": list(targets),
        "score": 5,
        "max_score": 7,
        "position_size": size,
        "risk_amount": 100.0,
        "rationale": ["teste"],
    }


def open_pos(portfolio, sig=None):
    sig = sig or long_signal_dict()
    sig_id = portfolio.store.save_signal(sig)
    portfolio.open_from_signal(sig_id, sig)
    return portfolio.open_positions()[0]


def test_open_and_list(portfolio):
    pos = open_pos(portfolio)
    assert pos["status"] == "open"
    assert pos["remaining_size"] == 20.0
    assert portfolio.equity == 10000.0


def test_stop_loss_full_loss(portfolio):
    pos = open_pos(portfolio)
    updated = portfolio.update_with_candle(pos, high=101.0, low=94.0)
    assert updated["status"] == "closed"
    # perdeu (100-95)*20 = 100
    assert abs(updated["realized_pnl"] + 100.0) < 1e-9
    assert abs(portfolio.equity - 9900.0) < 1e-9


def test_tp1_realizes_half_and_moves_breakeven(portfolio):
    pos = open_pos(portfolio)
    updated = portfolio.update_with_candle(pos, high=108.0, low=99.0)
    assert updated["status"] == "open"
    assert updated["stop"] == 100.0  # breakeven
    assert updated["remaining_size"] == 10.0
    # ganhou (107.5-100)*10 = 75
    assert abs(updated["realized_pnl"] - 75.0) < 1e-9


def test_full_cycle_tp1_tp2_tp3(portfolio):
    pos = open_pos(portfolio)
    pos = portfolio.update_with_candle(pos, high=108.0, low=99.0)   # TP1
    pos = portfolio.update_with_candle(pos, high=113.0, low=106.0)  # TP2
    pos = portfolio.update_with_candle(pos, high=121.0, low=112.0)  # TP3
    assert pos["status"] == "closed"
    # TP1: 7.5*10=75 | TP2: 12.5*5=62.5 | TP3: 20*5=100 → 237.5
    assert abs(pos["realized_pnl"] - 237.5) < 1e-9
    assert abs(portfolio.equity - 10237.5) < 1e-9


def test_breakeven_stop_after_tp1(portfolio):
    pos = open_pos(portfolio)
    pos = portfolio.update_with_candle(pos, high=108.0, low=99.0)   # TP1 + breakeven
    pos = portfolio.update_with_candle(pos, high=105.0, low=99.5)   # volta ao entry
    assert pos["status"] == "closed"
    # restante sai no breakeven sem perda extra: pnl mantém os 75 do TP1
    assert abs(pos["realized_pnl"] - 75.0) < 1e-9


def test_conservative_stop_first_same_candle(portfolio):
    """Candle que atinge stop E alvo: assume stop primeiro (conservador)."""
    pos = open_pos(portfolio)
    updated = portfolio.update_with_candle(pos, high=110.0, low=94.0)
    assert updated["status"] == "closed"
    assert updated["realized_pnl"] < 0


def test_short_position_cycle(portfolio):
    sig = long_signal_dict(entry=100.0, stop=105.0, targets=(92.5, 87.5, 80.0))
    sig["direction"] = "short"
    pos = open_pos(portfolio, sig)
    pos = portfolio.update_with_candle(pos, high=101.0, low=92.0)  # TP1 short
    assert pos["status"] == "open"
    assert pos["stop"] == 100.0
    assert abs(pos["realized_pnl"] - 75.0) < 1e-9  # (100-92.5)*10


def test_summary_metrics(portfolio):
    pos = open_pos(portfolio)
    portfolio.update_with_candle(pos, high=101.0, low=94.0)  # stop
    s = portfolio.summary()
    assert s["closed_trades"] == 1
    assert s["win_rate"] == 0.0
    assert s["equity"] == 9900.0

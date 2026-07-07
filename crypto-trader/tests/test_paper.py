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


def test_checked_until_marker_single_and_updated(portfolio):
    """O marcador de idempotência é único e sempre reflete o último candle."""
    pos = open_pos(portfolio)
    ts1, ts2 = "2024-01-02T00:00:00+00:00", "2024-01-02T04:00:00+00:00"
    pos = portfolio.update_with_candle(pos, high=101.0, low=99.0, candle_ts=ts1)
    pos = portfolio.update_with_candle(pos, high=101.0, low=99.0, candle_ts=ts2)
    markers = [e for e in pos["events"] if e["type"] == "checked_until"]
    assert len(markers) == 1
    assert markers[0]["ts"] == "2024-01-02T04:00:00+00:00"


def test_tick_no_write_when_nothing_crossed(portfolio):
    pos = open_pos(portfolio)
    updated, changed = portfolio.update_with_tick(pos, 101.0)
    assert changed is False
    assert updated["status"] == "open"
    assert updated["events"] == []


def test_tick_stop_fills_at_stop_level(portfolio):
    pos = open_pos(portfolio)
    updated, changed = portfolio.update_with_tick(pos, 94.2)  # abaixo do stop 95
    assert changed is True
    assert updated["status"] == "closed"
    # fill no nível do stop (95), não no preço do tick: perda exata de 100
    assert abs(updated["realized_pnl"] + 100.0) < 1e-9


def test_tick_tp1_breakeven_then_protected(portfolio):
    pos = open_pos(portfolio)
    pos, changed = portfolio.update_with_tick(pos, 108.0)   # cruza TP1
    assert changed and pos["stop"] == 100.0 and pos["remaining_size"] == 10.0
    pos, changed = portfolio.update_with_tick(pos, 99.9)    # volta ao entry
    assert changed and pos["status"] == "closed"
    assert abs(pos["realized_pnl"] - 75.0) < 1e-9           # só o lucro do TP1


def test_tick_crossing_multiple_levels_in_one_move(portfolio):
    pos = open_pos(portfolio)
    pos, changed = portfolio.update_with_tick(pos, 121.0)   # salta TP1+TP2+TP3
    assert changed and pos["status"] == "closed"
    # TP1: 7.5*10 | TP2: 12.5*5 | TP3: 20*5 → 237.5
    assert abs(pos["realized_pnl"] - 237.5) < 1e-9


def test_tick_short_direction(portfolio):
    sig = long_signal_dict(entry=100.0, stop=105.0, targets=(92.5, 87.5, 80.0))
    sig["direction"] = "short"
    pos = open_pos(portfolio, sig)
    pos, changed = portfolio.update_with_tick(pos, 92.0)    # cruza TP1 do short
    assert changed and pos["stop"] == 100.0
    assert abs(pos["realized_pnl"] - 75.0) < 1e-9


def test_summary_metrics(portfolio):
    pos = open_pos(portfolio)
    portfolio.update_with_candle(pos, high=101.0, low=94.0)  # stop
    s = portfolio.summary()
    assert s["closed_trades"] == 1
    assert s["win_rate"] == 0.0
    assert s["equity"] == 9900.0

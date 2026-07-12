import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Config
from app.execution.executors import BybitExecutor, DryRunExecutor, to_swap_symbol
from app.execution.manager import ExecutionManager
from app.paper.store import Store


def sd(symbol="BTC/USDT", direction="long"):
    return {
        "id": 1, "symbol": symbol, "direction": direction, "timeframe": "1h",
        "entry": 100.0, "stop": 95.0, "targets": [107.5, 112.5, 120.0],
        "position_size": 2.0, "suggested_leverage": 5,
        "created_at": datetime.now(UTC).isoformat(),
    }


def make_cfg(mode="manual", **over):
    ex = {
        "mode": mode, "approval_ttl_min": 15, "max_daily_loss_pct": 0.03,
        "max_trades_per_day": 6, "max_consecutive_errors": 3,
    }
    ex.update(over)
    return Config({"execution": ex, "risk": {"account_equity": 10000}})


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "exec.db")


def save_signal(store, d):
    d = dict(d)
    d.setdefault("trade_type", "day_trade")
    d.setdefault("score", 5)
    d.setdefault("max_score", 7)
    d.setdefault("risk_amount", 100.0)
    d.setdefault("rationale", [])
    d.setdefault("margin_required", 40.0)
    d.setdefault("leverage_rationale", "")
    return store.save_signal(d)


def test_to_swap_symbol():
    assert to_swap_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert to_swap_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"


def test_dry_run_builds_bracket_orders(store):
    ex = DryRunExecutor(store)
    result = ex.execute_signal(sd())
    orders = result["orders"]
    assert result["ok"] and result["dry_run"]
    assert orders["entry"]["side"] == "buy" and orders["entry"]["qty"] == 2.0
    assert orders["tp1"]["qty"] == 1.0 and orders["tp1"]["reduceOnly"]
    assert orders["tp2"]["qty"] == 0.5
    assert orders["stop"]["triggerPrice"] == 95.0 and orders["stop"]["qty"] == 2.0
    log_rows = store.execution_log_recent()
    assert log_rows[0]["action"] == "entry" and log_rows[0]["ok"]


def test_manager_off_mode_does_nothing(store):
    mgr = ExecutionManager(make_cfg("off"), store, DryRunExecutor(store))
    result = asyncio.run(mgr.handle_signal(sd(), 1))
    assert result["action"] == "off"
    assert store.execution_log_recent() == []


def test_manual_mode_creates_approval_and_notifies(store):
    notes = []

    async def notify(text, buttons):
        notes.append((text, buttons))

    mgr = ExecutionManager(make_cfg("manual"), store, DryRunExecutor(store), notify=notify)
    signal_id = save_signal(store, sd())
    result = asyncio.run(mgr.handle_signal(sd(), signal_id))
    assert result["action"] == "pending_approval"
    pending = store.pending_approvals(datetime.now(UTC).isoformat())
    assert len(pending) == 1 and pending[0]["symbol"] == "BTC/USDT"
    assert notes and "Aprovar" in notes[0][1][0][0]["text"]


def test_approve_executes_and_reject_does_not(store):
    executor = DryRunExecutor(store)
    mgr = ExecutionManager(make_cfg("manual"), store, executor)
    sig_id = save_signal(store, sd())
    asyncio.run(mgr.handle_signal(sd(), sig_id))
    approval = store.pending_approvals(datetime.now(UTC).isoformat())[0]

    result = asyncio.run(mgr.decide(approval["id"], approve=True, via="telegram"))
    assert result["ok"] and result["status"] == "approved"
    assert any(c["action"] == "execute_signal" for c in executor.calls)

    # segunda decisão sobre a mesma aprovação falha (atômico)
    again = asyncio.run(mgr.decide(approval["id"], approve=False, via="dashboard"))
    assert again["ok"] is False


def test_expired_approval_cannot_execute(store):
    mgr = ExecutionManager(make_cfg("manual"), store, DryRunExecutor(store))
    sig_id = save_signal(store, sd())
    past = datetime.now(UTC) - timedelta(minutes=30)
    approval_id = store.create_approval(
        sig_id, past.isoformat(), (past + timedelta(minutes=15)).isoformat()
    )
    result = asyncio.run(mgr.decide(approval_id, approve=True, via="telegram"))
    assert result["ok"] is False


def test_auto_mode_executes_directly(store):
    executor = DryRunExecutor(store)
    mgr = ExecutionManager(make_cfg("auto"), store, executor)
    result = asyncio.run(mgr.handle_signal(sd(), 1))
    assert result["action"] == "executed"
    assert executor.calls


def test_breaker_trips_on_max_trades(store):
    executor = DryRunExecutor(store)
    mgr = ExecutionManager(make_cfg("auto", max_trades_per_day=2), store, executor)
    asyncio.run(mgr.handle_signal(sd(), 1))
    asyncio.run(mgr.handle_signal(sd("ETH/USDT"), 2))
    result = asyncio.run(mgr.handle_signal(sd("SOL/USDT"), 3))
    assert result["action"] == "breaker"
    assert mgr.paused
    # pausado: próximo sinal nem tenta
    result = asyncio.run(mgr.handle_signal(sd("BNB/USDT"), 4))
    assert result["action"] == "skipped"
    mgr.resume()
    assert not mgr.paused


def test_breaker_trips_on_daily_loss(store):
    # posição fechada hoje com prejuízo acima do limite (3% de 10000 = 300)
    sig_id = save_signal(store, sd())
    pos_id = store.open_position(sig_id, {**sd(), "position_size": 2.0})
    store.update_position(
        pos_id, status="closed", realized_pnl=-400.0,
        closed_at=datetime.now(UTC).isoformat(),
    )
    mgr = ExecutionManager(make_cfg("auto"), store, DryRunExecutor(store))
    result = asyncio.run(mgr.handle_signal(sd("ETH/USDT"), 2))
    assert result["action"] == "breaker"
    assert any("perda diária" in t for t in result["tripped"])


def test_error_streak_pauses(store):
    class FailingExecutor(DryRunExecutor):
        def execute_signal(self, _sd):
            return {"ok": False, "error": "boom"}

    mgr = ExecutionManager(
        make_cfg("auto", max_consecutive_errors=2), store, FailingExecutor(store)
    )
    asyncio.run(mgr.handle_signal(sd(), 1))
    asyncio.run(mgr.handle_signal(sd("ETH/USDT"), 2))
    assert mgr.paused
    assert "erros" in store.get_setting("execution_pause_reason", "")


class FakeBybit:
    """Grava as chamadas para validar o mapeamento de ordens do BybitExecutor."""

    def __init__(self):
        self.calls = []
        self._id = 0

    def load_markets(self):
        self.calls.append(("load_markets",))

    def amount_to_precision(self, symbol, qty):
        return f"{qty:.3f}"

    def set_leverage(self, lev, symbol):
        self.calls.append(("set_leverage", lev, symbol))

    def create_order(self, symbol, otype, side, qty, price=None, params=None):
        self._id += 1
        self.calls.append(("create_order", symbol, otype, side, qty, price, params or {}))
        return {"id": f"ord{self._id}"}


def test_bybit_executor_order_mapping(store, monkeypatch):
    executor = BybitExecutor.__new__(BybitExecutor)
    executor.store = store
    executor.exchange = FakeBybit()
    executor.testnet = True
    executor._stop_order_ids = {}

    result = executor.execute_signal(sd(direction="short"))
    assert result["ok"]
    orders = [c for c in executor.exchange.calls if c[0] == "create_order"]
    assert len(orders) == 4
    entry, tp1, tp2, stop = orders
    assert entry[1] == "BTC/USDT:USDT" and entry[2] == "market" and entry[3] == "sell"
    assert tp1[3] == "buy" and tp1[6]["reduceOnly"] and tp1[5] == 107.5
    assert abs(tp1[4] - 1.0) < 1e-9 and abs(tp2[4] - 0.5) < 1e-9
    assert stop[6]["triggerPrice"] == 95.0 and stop[6]["reduceOnly"]
    assert ("set_leverage", 5, "BTC/USDT:USDT") in executor.exchange.calls
    assert executor._stop_order_ids["BTC/USDT:USDT"] == "ord4"

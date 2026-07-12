import asyncio

import pytest

from app.alerts.telegram_bot import TelegramBot


class FakeExecution:
    def __init__(self):
        self.mode = "off"
        self.paused_flag = False
        self.decisions = []

    def set_mode(self, mode):
        self.mode = mode

    @property
    def paused(self):
        return self.paused_flag

    def pause(self, reason=""):
        self.paused_flag = True

    def resume(self):
        self.paused_flag = False

    async def decide(self, approval_id, approve, via):
        self.decisions.append((approval_id, approve, via))
        return {"ok": True, "status": "approved" if approve else "rejected"}

    def status(self):
        return {
            "mode": self.mode, "paused": self.paused_flag, "pause_reason": "",
            "executor": "dry-run",
            "breaker": {"pnl_today": 0, "max_daily_loss": 300, "entries_today": 0,
                        "max_trades_per_day": 6, "error_streak": 0, "tripped": []},
            "pending_approvals": [],
        }


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    execution = FakeExecution()
    b = TelegramBot(engine=None, execution=execution)
    sent = []

    async def fake_send(text, buttons=None):
        sent.append(text)
        return True

    b.send = fake_send
    b.sent = sent
    b.exec = execution
    return b


def run(coro):
    return asyncio.run(coro)


def test_only_configured_chat_is_accepted(bot):
    update = {"message": {"chat": {"id": 999}, "text": "/modo auto"}}
    run(bot._handle_update(update))
    assert bot.exec.mode == "off"      # comando de chat estranho ignorado
    assert bot.sent == []


def test_modo_command_switches_mode(bot):
    update = {"message": {"chat": {"id": 111}, "text": "/modo manual"}}
    run(bot._handle_update(update))
    assert bot.exec.mode == "manual"
    assert "manual" in bot.sent[-1]


def test_pause_and_resume_commands(bot):
    run(bot._handle_update({"message": {"chat": {"id": 111}, "text": "/pausar"}}))
    assert bot.exec.paused_flag is True
    run(bot._handle_update({"message": {"chat": {"id": 111}, "text": "/retomar"}}))
    assert bot.exec.paused_flag is False


def test_unknown_command_sends_help(bot):
    run(bot._handle_update({"message": {"chat": {"id": 111}, "text": "/qualquer"}}))
    assert "Comandos" in bot.sent[-1]


def test_approval_callback_routes_to_manager(bot, monkeypatch):
    async def no_answer(*a, **k):
        class R:
            status_code = 200
        return R()

    monkeypatch.setattr(bot, "_http", lambda: type("C", (), {"post": no_answer})())
    callback = {
        "id": "cb1", "data": "approve:7",
        "message": {"chat": {"id": 111}},
    }
    run(bot._handle_update({"callback_query": callback}))
    assert bot.exec.decisions == [(7, True, "telegram")]


def test_callback_from_wrong_chat_ignored(bot):
    callback = {"id": "cb1", "data": "approve:7", "message": {"chat": {"id": 999}}}
    run(bot._handle_update({"callback_query": callback}))
    assert bot.exec.decisions == []

from types import SimpleNamespace

from app import auth


def fake_request(headers=None, query=None):
    return SimpleNamespace(headers=headers or {}, query_params=query or {})


def test_no_token_configured_allows_all(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    assert auth.token_configured() is False
    assert auth.request_authorized(fake_request()) is True


def test_bearer_header_accepted(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "segredo123")
    req = fake_request(headers={"authorization": "Bearer segredo123"})
    assert auth.request_authorized(req) is True


def test_query_param_accepted_for_sse(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "segredo123")
    assert auth.request_authorized(fake_request(query={"token": "segredo123"})) is True


def test_wrong_or_missing_token_rejected(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "segredo123")
    assert auth.request_authorized(fake_request()) is False
    assert auth.request_authorized(
        fake_request(headers={"authorization": "Bearer errado"})
    ) is False
    assert auth.request_authorized(fake_request(query={"token": ""})) is False

"""Carrega config.yaml e variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# CRYPTO_TRADER_CONFIG permite apontar um config alternativo (testes, múltiplos perfis)
CONFIG_PATH = Path(os.environ.get("CRYPTO_TRADER_CONFIG", PROJECT_ROOT / "config.yaml"))
ENV_PATH = PROJECT_ROOT / ".env"


def _load_env(path: Path = ENV_PATH) -> None:
    """Parser mínimo de .env — evita dependência extra."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class Config:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, path: str, default: Any = None) -> Any:
        """Acesso por caminho pontuado: cfg.get('strategy.rsi_period')."""
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def validate(cfg: Config) -> None:
    """Falha cedo com mensagem clara para configuração sem sentido."""
    problems: list[str] = []
    if not cfg.get("market.pairs"):
        problems.append("market.pairs não pode ser vazio")
    if not cfg.get("market.timeframes"):
        problems.append("market.timeframes não pode ser vazio")
    risk = cfg.get("risk.risk_per_trade", 0.01)
    if not 0 < risk <= 0.1:
        problems.append(f"risk.risk_per_trade deve estar em (0, 0.1]; recebido {risk}")
    equity = cfg.get("risk.account_equity", 10000)
    if equity <= 0:
        problems.append(f"risk.account_equity deve ser positivo; recebido {equity}")
    rr = cfg.get("strategy.min_risk_reward", 1.5)
    if rr < 1:
        problems.append(f"strategy.min_risk_reward deve ser >= 1; recebido {rr}")
    candles = cfg.get("market.candles", 400)
    if not 60 <= candles <= 1000:
        problems.append(f"market.candles deve estar em [60, 1000]; recebido {candles}")
    max_pos = cfg.get("risk.max_open_positions", 5)
    if max_pos < 1:
        problems.append(f"risk.max_open_positions deve ser >= 1; recebido {max_pos}")
    max_lev = cfg.get("risk.max_leverage", 10)
    if not 1 <= max_lev <= 100:
        problems.append(f"risk.max_leverage deve estar em [1, 100]; recebido {max_lev}")
    liq_buffer = cfg.get("risk.liq_buffer", 3.0)
    if liq_buffer < 1.5:
        problems.append(f"risk.liq_buffer deve ser >= 1.5; recebido {liq_buffer}")
    exec_mode = cfg.get("execution.mode", "off")
    if exec_mode not in ("off", "manual", "auto"):
        problems.append(f"execution.mode deve ser off|manual|auto; recebido {exec_mode!r}")
    daily_loss = cfg.get("execution.max_daily_loss_pct", 0.03)
    if not 0 < daily_loss <= 0.2:
        problems.append(
            f"execution.max_daily_loss_pct deve estar em (0, 0.2]; recebido {daily_loss}"
        )
    ttl = cfg.get("execution.approval_ttl_min", 15)
    if ttl < 1:
        problems.append(f"execution.approval_ttl_min deve ser >= 1; recebido {ttl}")
    if cfg.get("screener.enabled", True):
        max_pairs = cfg.get("screener.max_pairs", 25)
        if not 1 <= max_pairs <= 100:
            problems.append(f"screener.max_pairs deve estar em [1, 100]; recebido {max_pairs}")
        refresh = cfg.get("screener.refresh_hours", 6)
        if refresh < 1:
            problems.append(f"screener.refresh_hours deve ser >= 1; recebido {refresh}")
        min_vol = cfg.get("screener.min_quote_volume_24h", 20_000_000)
        if min_vol < 0:
            problems.append("screener.min_quote_volume_24h não pode ser negativo")
    if problems:
        raise ValueError("Configuração inválida:\n  - " + "\n  - ".join(problems))


def db_path(cfg: Config) -> Path:
    """Caminho do SQLite: CRYPTO_TRADER_DB_DIR (deploy/volume) sobrepõe o dir."""
    p = Path(cfg.get("paper.db_path", "paper_trading.db"))
    env_dir = os.environ.get("CRYPTO_TRADER_DB_DIR")
    if env_dir:
        return Path(env_dir) / p.name
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_config(path: Path = CONFIG_PATH) -> Config:
    _load_env()
    with open(path) as fh:
        cfg = Config(yaml.safe_load(fh))
    validate(cfg)
    return cfg

"""Carrega config.yaml e variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
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
    if problems:
        raise ValueError("Configuração inválida:\n  - " + "\n  - ".join(problems))


def load_config(path: Path = CONFIG_PATH) -> Config:
    _load_env()
    with open(path) as fh:
        cfg = Config(yaml.safe_load(fh))
    validate(cfg)
    return cfg

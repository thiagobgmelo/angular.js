# 01 — Visão e planejamento

## Objetivo do produto

Um sistema de trading de criptomoedas que aplica **análise gráfica e teorias
validadas de análise técnica** para produzir recomendações completas de trade:

- **tipo de trade** (swing trade ou day trade, conforme o timeframe)
- **direção** (long/short) com o racional legível de cada critério
- **ponto de entrada**, **stop loss** e **alvos de lucro** (TP1/TP2/TP3)
- **tamanho de posição** calculado por gestão de risco

conectado a ferramentas amplamente usadas no mercado (ccxt, TradingView
Lightweight Charts, Telegram), com execução **simulada** (paper trading),
backtest para validar a estratégia sobre o histórico e dashboard web em tempo
real.

> ⚠️ O sistema é educacional e opera 100% em simulação. Nenhuma ordem real é
> enviada. Não é recomendação de investimento.

## Requisitos originais (pedido do usuário)

1. Considerar análise gráfica e teorias validadas/comprovadas
2. Identificar o tipo de trade, possíveis entradas e saídas
3. Definir pontos de segurança (stop) e alvos de lucro
4. Conectar ferramentas e sistemas amplamente utilizados no mundo
5. (iteração 2) Aplicar boas práticas de engenharia, QA e testes de segurança
6. (iteração 3) Otimizar o tempo de atualização/sincronização dos dados —
   crucial para a qualidade das entradas

## Decisões de escopo alinhadas com o usuário

Antes da construção, quatro perguntas definiram o escopo (as justificativas
técnicas detalhadas estão nos [ADRs](02-decisoes-de-arquitetura.md)):

| Pergunta | Decisão | Motivação |
|---|---|---|
| Nível de operação | **Análise + paper trading** (execução real fica para o futuro) | Zero risco financeiro enquanto a estratégia é validada; a arquitetura deixa o ponto de extensão pronto |
| Stack | **Python** | Ecossistema padrão de trading quantitativo (ccxt, pandas, backtesting) |
| Fonte de dados | **Binance via ccxt** | Exchange mais líquida do mundo; API pública de candles sem chave; ccxt permite trocar de exchange por config |
| Interface | **Dashboard web + alertas Telegram** | Acompanhamento visual (gráficos TradingView) + notificação ativa de sinais |

O usuário também perguntou se o **TradingView** entraria no projeto — a
resposta está no [ADR-004](02-decisoes-de-arquitetura.md#adr-004--tradingview-entra-via-lightweight-charts): o
TradingView não oferece API pública de dados, mas entra via **Lightweight
Charts**, a biblioteca open-source oficial deles para gráficos, usada no
dashboard.

## Fases entregues

### Fase 1 — Construção (commit `1e24ebd`)

Sistema funcional completo: wrapper ccxt, indicadores próprios em pandas,
níveis de suporte/resistência + Fibonacci, padrões de candle, motor de
confluência de 7 critérios, risk manager (1% por trade), paper trading com
SQLite, backtest com métricas, dashboard (FastAPI + Lightweight Charts),
alertas Telegram, CLI e 34 testes.

### Fase 2 — QA e segurança (commit `ed4168c`)

Auditoria do próprio código: 6 correções de segurança (bind localhost, anti-XSS,
whitelist SQL, lock de thread no SQLite, validação de parâmetros da API, escape
no Telegram), 5 de robustez (destaque: idempotência do processamento de candles
nas posições), lint ruff com regras de segurança, dependências pinadas e testes
de backtest. Suíte foi a 39 testes.

### Fase 3 — Tempo real (commit `151f890`)

Diagnóstico honesto: o polling de 300 s era inadequado para trading. Nova
arquitetura orientada a eventos: websocket (ccxt.pro) → cache em memória →
eventos de candle fechado (análise dispara em sub-segundo) e de tick (stops
das posições protegidos ao vivo) → SSE para o dashboard. Medições da
verificação: evento de candle 0,20 s após o fechamento, análise em 55 ms,
API servida do cache em ~45 ms. Suíte foi a 54 testes.

## Roadmap (não implementado, por decisão)

1. **Execução real de ordens** — ponto de extensão: a máquina de estados de
   `app/paper/portfolio.py` viraria uma interface com duas implementações
   (paper e real via ccxt com API keys). Pré-requisito sensato: meses de
   resultados de paper trading e backtests convincentes.
2. **Otimização de parâmetros** — varrer limiar de score, períodos e razões de
   R:R sobre o backtest (walk-forward para evitar overfitting).
3. **Autenticação na API** — necessária apenas se o dashboard for exposto além
   de localhost (ver [ADR-008](02-decisoes-de-arquitetura.md#adr-008--bind-em-localhost-sem-autenticação-na-v1)).
4. **Custos de execução no backtest** — slippage e taxas (hoje não modelados;
   ver limitações em [04 — Funcionamento](04-funcionamento.md#limitações-conhecidas)).

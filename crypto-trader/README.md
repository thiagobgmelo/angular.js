# Crypto Trader — Análise Técnica + Paper Trading

Sistema de trading de criptomoedas que aplica **teorias clássicas e validadas de
análise técnica** para identificar oportunidades e entregar sinais completos:
tipo de trade (swing/day trade), direção (long/short), preço de entrada, stop
loss, alvos de lucro (TP1/TP2/TP3) e tamanho de posição — com execução simulada
(paper trading), backtest e dashboard web.

> ⚠️ **Aviso**: projeto educacional. Não é recomendação de investimento. O modo
> de operação é 100% simulado (paper trading) — nenhuma ordem real é enviada.

## Ferramentas conectadas

| Ferramenta | Papel |
|---|---|
| [ccxt](https://github.com/ccxt/ccxt) | Dados OHLCV da Binance (e 100+ exchanges, trocável no config) |
| pandas / numpy | Séries de candles e indicadores |
| FastAPI + Uvicorn | API REST e dashboard |
| [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) | Gráficos de candles no navegador |
| SQLite | Sinais, posições e curva de equity |
| Telegram Bot API | Alertas de sinais (opcional) |

## Instalação

```bash
cd crypto-trader
pip install -r requirements.txt
```

Os dados públicos de candles da Binance não exigem conta nem API key.

## Uso

```bash
python -m app.main scan                  # uma passada em todos os pares/timeframes
python -m app.main watch                 # scanner contínuo (intervalo no config.yaml)
python -m app.main analyze BTC/USDT 4h   # análise detalhada de um par
python -m app.main backtest BTC/USDT 4h 1500
python -m app.main serve                 # dashboard em http://localhost:8000
```

**Modo demo (offline)**: qualquer comando com `EXCHANGE_ID=demo` usa dados
sintéticos determinísticos — útil para conhecer o sistema sem internet:

```bash
EXCHANGE_ID=demo python -m app.main serve
```

## A estratégia — motor de confluência

Cada critério a favor de um lado soma 1 ponto (máx. 7). Sinal é emitido quando o
score atinge o limiar (`strategy.score_threshold`, padrão 4), há dominância
clara sobre o lado oposto (≥ 2 pontos) e o R:R até o TP1 é ≥ 1.5.

1. **Estrutura de mercado (Teoria de Dow)** — topos e fundos ascendentes ou
   descendentes via fractais de swing
2. **Tendência por EMAs 50/200** — posição do preço relativa às médias
3. **RSI(14) de Wilder** — sobrecompra/sobrevenda *a favor da tendência* e
   divergências preço×RSI para reversões
4. **MACD(12,26,9)** — cruzamento da linha de sinal e histograma
5. **Suporte/Resistência + Fibonacci** — pivôs agrupados em zonas; retrações
   38,2% / 50% / 61,8% da última perna
6. **Padrões de candle (Nison)** — engolfo, martelo, estrela cadente; só pontuam
   em região de interesse (zona de S/R, Fibonacci ou RSI esticado)
7. **Volume** — candle confirmado por volume acima da média de 20 períodos

### Gestão de risco

- Risco fixo de **1% do capital por trade** (`risk.risk_per_trade`) → tamanho de
  posição calculado automaticamente
- **Stop**: swing recente ± 1×ATR(14), o que for mais conservador
- **Alvos**: TP1 = 1,5R (realiza 50% e move stop para breakeven),
  TP2 = 2,5R (realiza 25%), TP3 = próxima zona de S/R (restante)
- Simulação conservadora: se stop e alvo saem no mesmo candle, assume o stop

## Dashboard

`python -m app.main serve` e abra <http://localhost:8000>:

- Gráfico de candles com EMA 50/200 e níveis do sinal (entrada, stop, alvos)
- Painel do sinal atual com score e racional de cada critério
- Sinais recentes e carteira paper (equity, win rate, profit factor, posições)
- Botão **Escanear tudo** dispara o scanner por cima de todos os pares

## Alertas no Telegram (opcional)

1. Crie um bot com o [@BotFather](https://t.me/BotFather) e copie o token
2. Descubra seu chat id (ex.: [@userinfobot](https://t.me/userinfobot))
3. `cp .env.example .env` e preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`

Todo sinal novo do scanner é enviado formatado para o seu chat.

## Configuração

Tudo em [`config.yaml`](config.yaml): exchange, pares, timeframes, parâmetros da
estratégia (períodos, limiar de score, R:R mínimo) e risco (capital, % por
trade, máx. de posições).

## Testes

```bash
python -m pytest tests/
```

34 testes cobrem indicadores (valores de referência), níveis/estrutura,
estratégia (cenários sintéticos de tendência), risk manager e ciclo completo do
paper trading (TP1 → breakeven → TP2 → TP3 / stop).

## Estrutura

```
app/
  data/exchange.py      # ccxt + provedor demo offline
  analysis/             # indicadores, níveis/Fibonacci, padrões, estratégia
  risk/manager.py       # position sizing e validação R:R
  paper/                # carteira simulada + SQLite
  backtest/engine.py    # backtest com métricas (win rate, PF, drawdown)
  alerts/telegram.py    # notificações
  scanner.py            # varredura pares × timeframes
  server.py             # API REST + dashboard
dashboard/              # front (Lightweight Charts)
tests/
```

## Roadmap

- Execução real de ordens via ccxt (ponto de extensão em `app/paper/portfolio.py`)
- Websocket para atualização em tempo real
- Otimização de parâmetros da estratégia sobre o backtest

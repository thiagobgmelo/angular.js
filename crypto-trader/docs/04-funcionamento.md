# 04 — Funcionamento da plataforma

Como o sistema funciona de ponta a ponta: componentes, estratégia, risco,
ciclo de vida de um trade e operação no dia a dia.

## Visão geral dos componentes

```
                    ┌─────────────────────────────────────────────┐
                    │                 EXCHANGE                    │
                    │   Binance (ou outra via ccxt) · modo demo   │
                    └───────────────┬─────────────────────────────┘
              websocket (watch_ohlcv + watch_ticker) │ REST (histórico/batch)
                    ┌───────────────▼─────────────────────────────┐
                    │        MarketFeed (app/data/feed.py)        │
                    │  cache em memória: candles por par×tf +     │
                    │  último preço · watchdog · reconexão        │
                    └───────┬───────────────────┬─────────────────┘
                candle fechou│                  │tick de preço
                    ┌────────▼──────────────────▼─────────────────┐
                    │      TradingEngine (app/engine.py)          │
                    │                                             │
                    │  candle → strategy.analyze (7 critérios)    │
                    │         → RiskManager (1%/trade, R:R)       │
                    │         → sinal + posição paper + Telegram  │
                    │                                             │
                    │  tick   → update_with_tick nas posições     │
                    │           (stop/TP1/TP2/TP3 ao vivo)        │
                    └────────┬──────────────────┬─────────────────┘
                             │                  │ eventos (bus)
                    ┌────────▼───────┐  ┌───────▼─────────────────┐
                    │ Store (SQLite) │  │  FastAPI (app/server.py)│
                    │ sinais/posições│  │  REST + SSE /api/stream │
                    │ equity         │  └───────┬─────────────────┘
                    └────────────────┘          │
                                       ┌────────▼────────────────┐
                                       │ Dashboard (Lightweight  │
                                       │ Charts + EventSource)   │
                                       └─────────────────────────┘
```

## Universo monitorado — screener automático

Quais moedas o watcher acompanha não é uma lista fixa: um **screener**
([ADR-013](02-decisoes-de-arquitetura.md#adr-013--screener-de-universo-com-critérios-objetivos-v4))
seleciona o universo na inicialização e o re-avalia a cada `refresh_hours`
(default 6 h), com critérios objetivos definidos em `screener:` no config:

1. Mercado **spot USDT ativo** (sem futuros, sem tokens alavancados UP/DOWN/BULL/BEAR)
2. **Volume 24h ≥ US$ 20M** — liquidez como proxy de confiança
3. **Histórico ≥ 180 dias** — exclui listagens recentes sem gráfico analisável
4. Sem stablecoins/fiat como base (USDC, FDUSD, DAI, EUR...)
5. Ranking por volume, corte no **top 25** (limite de streams/CPU)
6. **Majors sempre presentes** (`always_include`: BTC, ETH, SOL, BNB) + denylist manual

Cada par do universo ganha seus próprios streams websocket (candles em todos
os timeframes + ticker), rodando **em paralelo** como tasks assíncronas
independentes. Quando o universo muda no re-screening, os streams são
adicionados/removidos a quente e o dashboard atualiza o seletor via evento
SSE `universe`. Proteção: **par com posição paper aberta nunca sai do
monitoramento** (o stop precisa continuar protegido).

## A estratégia — motor de confluência

A cada **candle fechado** (nunca em formação — ver
[ADR-005](02-decisoes-de-arquitetura.md#adr-005--decisão-em-candle-fechado--confluência-com-dominância)), sete critérios são
avaliados. Cada um a favor de um lado soma 1 ponto:

| # | Critério | Teoria por trás | O que pontua |
|---|---|---|---|
| 1 | Estrutura de mercado | Teoria de Dow | Topos e fundos ascendentes (long) ou descendentes (short), via fractais de swing |
| 2 | Tendência por médias | Seguimento de tendência | Preço acima de EMA50 > EMA200 (long); abaixo de EMA50 < EMA200 (short) |
| 3 | RSI(14) de Wilder | Momentum / reversão | Sobrevenda **a favor da tendência** (pullback); contra a tendência só com divergência preço×RSI |
| 4 | MACD(12,26,9) | Momentum (Appel) | Linha MACD acima/abaixo da linha de sinal com histograma confirmando |
| 5 | Localização | Suporte/resistência + Fibonacci | Preço testando zona de S/R (pivôs agrupados) ou retração 38,2/50/61,8% da última perna |
| 6 | Padrão de candle | Price action (Nison) | Engolfo, martelo, estrela cadente — só vale **em região de interesse** (S/R, Fib ou RSI esticado) |
| 7 | Volume | Confirmação | Volume acima da média de 20 períodos confirmando o candle |

**Emissão do sinal** exige simultaneamente:
1. score do lado ≥ `strategy.score_threshold` (default **4/7**)
2. dominância: score do lado ≥ score oposto + 2
3. R:R até o TP1 ≥ `strategy.min_risk_reward` (default **1,5**)

**Tipo de trade** pelo timeframe analisado: 4h/1d → *swing trade*;
15m/1h → *day trade*. Cada sinal carrega o racional legível (a lista dos
critérios que pontuaram) — visível no dashboard e no alerta do Telegram.

## Gestão de risco (RiskManager)

- **Risco fixo por trade**: 1% do capital (`risk.risk_per_trade`). O tamanho
  da posição é calculado para que, se o stop for atingido, a perda seja
  exatamente esse valor.
- **Stop**: swing recente ± 1×ATR(14) — o que for mais conservador — com
  margem de 0,1 ATR.
- **Alvos**: TP1 = 1,5R · TP2 = 2,5R · TP3 = próxima zona de S/R (ou 4R).
- **Máximo de posições simultâneas**: `risk.max_open_positions` (default 5).
- Sinal que não passa no R:R mínimo é descartado antes de virar posição.

## Ciclo de vida de um trade (paper)

```
candle fecha ──► análise (confluência) ──► sinal aprovado pelo risco
                                                 │
                              posição aberta (100% do tamanho)
                                                 │
                    tick cruza TP1 ──► realiza 50% · stop → breakeven
                    tick cruza TP2 ──► realiza 25%
                    tick cruza TP3 ──► fecha o restante        ┐
                    tick cruza stop ──► fecha o restante       ├─► métricas:
                                                               ┘   win rate,
                                              equity/PnL atualizados,  profit factor,
                                              evento SSE p/ dashboard   drawdown
```

Regras de realismo (viés conservador — [ADR-006](02-decisoes-de-arquitetura.md#adr-006--paper-trading-com-regras-conservadoras)):
fills sempre no **nível exato** do stop/alvo; com granularidade de candle
(backtest/backfill), stop e alvo no mesmo candle → assume stop primeiro.

## Modos de operação

| Comando | O que faz | Fonte de dados |
|---|---|---|
| `python -m app.main serve [porta]` | API + dashboard + engine em tempo real | Websocket (cache em memória) |
| `python -m app.main watch` | Engine em tempo real sem servidor (headless, com Telegram) | Websocket |
| `python -m app.main scan` | Uma passada batch em todos os pares | REST |
| `python -m app.main analyze PAR TF` | Análise detalhada de um par no terminal | REST |
| `python -m app.main backtest PAR TF [n]` | Estratégia sobre o histórico + métricas | REST (paginado) |
| Qualquer um com `EXCHANGE_ID=demo` | Dados sintéticos determinísticos, offline | Simulador |

Variáveis de ambiente: `HOST` (bind do servidor; default `127.0.0.1`),
`EXCHANGE_ID` (sobrepõe a exchange do config), `CRYPTO_TRADER_CONFIG`
(config alternativo), `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (via `.env`).

## API

| Endpoint | Descrição |
|---|---|
| `GET /api/config` | Pares e timeframes configurados |
| `GET /api/ohlcv?symbol&timeframe&limit` | Candles + EMAs, do cache (~ms) |
| `GET /api/analysis?symbol&timeframe` | Contexto completo da análise + sinal se houver |
| `GET /api/signals?limit` | Sinais registrados |
| `GET /api/portfolio` | Resumo da carteira, posições abertas/fechadas, curva de equity |
| `POST /api/scan` | Análise imediata de todos os pares a partir do cache |
| `GET /api/backtest?symbol&timeframe&candles` | Backtest sob demanda |
| `GET /api/stream` | **SSE**: eventos `tick`, `candle`, `signal`, `position` |
| `GET /api/health` | Idade do dado por stream, contadores do engine, assinantes SSE |

Validações: `symbol`/`timeframe` restritos ao config (422 fora disso);
limites numéricos com faixas máximas.

## Observabilidade de latência

Em trading, dado velho é dado errado. Três instrumentos:

1. **`GET /api/health`** — por stream: quantidade de candles em cache, idade
   do último evento, flag `stale`; por símbolo: último preço e idade; do
   engine: ticks/candles/sinais processados e duração da última análise.
2. **Badge no dashboard** — "ao vivo · <1s" (verde) / "stale" ou "sem stream —
   polling" (vermelho), atualizado a cada segundo.
3. **Watchdog interno** — stream sem eventos além de `feed.stale_after_s`
   (default 90 s) gera WARNING no log e reinício automático das conexões.

## Configuração (config.yaml)

```yaml
exchange:   # qual exchange (ccxt id) e rate limiting
market:     # pares, timeframes e profundidade de candles da análise
strategy:   # limiar de score, R:R mínimo, períodos dos indicadores,
            # tolerância das zonas de S/R
risk:       # capital da carteira paper, % de risco por trade, máx. posições
paper:      # caminho do SQLite
feed:       # websocket|rest, throttle de ticks, limite de staleness
```

Cada chave tem default sensato no código; o `validate()` rejeita configurações
sem sentido (risco fora de (0, 10%], listas vazias etc.) na inicialização.

## Limitações conhecidas

Registradas para manter as expectativas honestas:

1. **Paper trading apenas** — nenhuma ordem real é enviada. Execução real é
   roadmap e exigirá gestão de chaves de API e tratamento de erros de ordem.
2. **Sem autenticação na API** — mitigado pelo bind em localhost
   ([ADR-008](02-decisoes-de-arquitetura.md#adr-008--bind-em-localhost-sem-autenticação-na-v1)); não exponha sem proxy
   autenticado.
3. **Backtest sem slippage e taxas** — os resultados são otimistas nessa
   dimensão (o viés conservador do stop compensa parcialmente). Adicionar
   custos de execução está no roadmap.
4. **Estratégia não otimizada** — os parâmetros (limiar 4/7, períodos
   clássicos) são os canônicos da literatura, não o resultado de otimização.
   Otimizar sem walk-forward produziria overfitting; foi deixado de fora de
   propósito.
5. **Latência real depende da rede** — as medições de sub-segundo foram feitas
   com o feed demo; com a Binance real, soma-se a latência de rede até a
   exchange (~50–300 ms típicos).
6. **Um processo, um usuário** — SQLite + cache em memória pressupõem uma
   única instância rodando por banco.

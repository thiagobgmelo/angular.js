# Crypto Trader — Análise Técnica + Paper Trading

Sistema de trading de criptomoedas que aplica **teorias clássicas e validadas de
análise técnica** para identificar oportunidades e entregar sinais completos:
tipo de trade (swing/day trade), direção (long/short), preço de entrada, stop
loss, alvos de lucro (TP1/TP2/TP3) e tamanho de posição — com execução simulada
(paper trading), backtest e dashboard web.

> ⚠️ **Aviso**: projeto educacional. Não é recomendação de investimento. O modo
> de operação é 100% simulado (paper trading) — nenhuma ordem real é enviada.

📚 **Documentação completa em [`docs/`](docs/README.md)**: visão e
planejamento, decisões de arquitetura (ADRs) com os porquês, histórico de
ajustes e o funcionamento detalhado da plataforma.

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

**Watcher em background**: o `watch` é headless (sinais via log + Telegram).
Para rodar continuamente:

```bash
nohup python -m app.main watch >> watcher.log 2>&1 &   # simples
# ou, para produção pessoal, um service do systemd:
#   ExecStart=/usr/bin/python3 -m app.main watch
#   WorkingDirectory=/caminho/para/crypto-trader
#   Restart=on-failure
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

## Arquitetura de dados — latência otimizada

Em trading, a idade do dado define a qualidade da entrada. O modo ao vivo
(`serve`/`watch`) usa **websocket da exchange (ccxt.pro)** alimentando um cache
em memória que emite eventos — nada de polling:

| Caminho | Latência |
|---|---|
| Reação ao fechamento de candle (gatilho da estratégia) | **< 1 s** (evento websocket) |
| Stop/alvos das posições paper | **tick a tick** (throttle configurável, fill no nível exato) |
| Dashboard | **push via SSE** — candle corrente se move em tempo real |
| `/api/ohlcv` e `/api/analysis` | **milissegundos** (cache em memória, zero I/O de rede) |

- **Universo automático (screener)**: as moedas monitoradas são selecionadas
  por critérios objetivos — spot USDT, volume 24h ≥ US$ 20M, histórico ≥ 180
  dias, sem stablecoins/alavancados — ranqueadas por volume (top 25), com as
  majors (BTC, ETH, SOL, BNB) sempre presentes. Re-avaliação a cada 6 h com
  streams adicionados/removidos a quente; par com posição aberta nunca sai.
  Tudo configurável em `screener:` no config (`enabled: false` = lista fixa).
- A decisão da estratégia continua sendo tomada **em candle fechado** (correto
  metodologicamente); o que é instantâneo é a *reação* ao fechamento e a
  proteção das posições com o preço vivo.
- **Resiliência**: reconexão automática com backoff; watchdog reinicia streams
  parados (`feed.stale_after_s`); sem websocket, `feed.mode: rest` faz fetch
  alinhado ao relógio dos candles (2 s após cada fechamento teórico).
- **Observabilidade**: `GET /api/health` mostra a idade do dado por stream,
  contadores de eventos e o tempo da última análise; o dashboard exibe um badge
  de latência ("ao vivo · <1s").
- Comandos batch (`scan`, `analyze`, `backtest`) continuam via REST simples.

## Dashboard

`python -m app.main serve` e abra <http://localhost:8000>:

- Gráfico com o **cenário completo**: EMAs, zonas de S/R, níveis de Fibonacci,
  marcador do padrão de candle e níveis do sinal (entrada, stop, TP1–3, liq.)
- **Checklist dos 7 critérios** com ✓/✗ por lado e a leitura de cada um —
  os porquês de toda sugestão de entrada
- **Radar de oportunidades em formação**: avisos com timestamp do que está
  quase virando sinal (e o que falta), link "abrir gráfico" e selo
  "✓ confirmada" quando a formação se completa; histórico persistido em
  `/api/radar` para análise futura
- Sinal com **alavancagem sugerida** (perpétuos): valor, margem imobilizada,
  liquidação estimada e o racional — regra: máxima alavancagem que mantém a
  liquidação ≥ 3× além do stop, teto 10x (`risk.max_leverage`)
- Sinais recentes e carteira paper (equity, win rate, profit factor, posições)
- Botão **Escanear tudo** dispara a análise imediata de todos os pares

## Execução real (Bybit perpétuos) e bot do Telegram

- **Modos alternáveis em runtime**: `off` (só paper, default) → `manual`
  (aprovação com botões no Telegram/dashboard, validade 15 min) → `auto`
  (circuit breaker: perda diária máx 3%, 6 entradas/dia, pausa em erros).
  Troque com `/modo` no Telegram, no card Execução ou via API.
- **Sem chaves = dry-run**: simula as ordens e audita tudo em
  `execution_log`; com `BYBIT_API_KEY/SECRET` no `.env` conecta na Bybit.
  Valide sempre num ambiente de teste primeiro — `BYBIT_DEMO=1` usa o **Demo
  Trading** (`api-demo.bybit.com`, mesma UTA do site real, mais fiel à
  produção; **recomendado**) e `BYBIT_TESTNET=1` usa o **testnet.bybit.com**
  (sandbox à parte). A chave precisa ter sido criada no mesmo ambiente. Para
  dinheiro real: os dois flags em `0`.
- **Bot bidirecional**: `/status`, `/posicoes`, `/radar`, `/modo`, `/pausar`,
  `/retomar` — só obedece ao `TELEGRAM_CHAT_ID` configurado. Setup guiado em
  [docs/06-telegram.md](docs/06-telegram.md); valide com
  `python -m app.main telegram-test` e `python -m app.main exec-test`.

## Deploy (uso e acesso remoto)

**Um comando numa VPS Ubuntu/Debian nova**:

```bash
git clone <SEU_FORK> app && cd app/crypto-trader && bash deploy.sh
```

O `deploy.sh` instala Docker, configura firewall, gera o `API_TOKEN`,
pergunta domínio/Telegram/Bybit e sobe tudo (app + Caddy com HTTPS
automático). Runbook completo, incluindo opções de **custo zero** (Oracle
Cloud Always Free / Tailscale): [docs/05-deploy.md](docs/05-deploy.md).

## Alertas no Telegram (opcional)

1. Crie um bot com o [@BotFather](https://t.me/BotFather) e copie o token
2. Descubra seu chat id (ex.: [@userinfobot](https://t.me/userinfobot))
3. `cp .env.example .env` e preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`

Todo sinal novo do scanner é enviado formatado para o seu chat.

## Configuração

Tudo em [`config.yaml`](config.yaml): exchange, pares, timeframes, parâmetros da
estratégia (períodos, limiar de score, R:R mínimo) e risco (capital, % por
trade, máx. de posições).

## Segurança

- O servidor escuta **somente em `127.0.0.1`** por padrão. Para expor na rede
  (`HOST=0.0.0.0 python -m app.main serve`), saiba que a API não tem
  autenticação e possui um endpoint mutável (`POST /api/scan`) — faça isso
  apenas em rede confiável ou atrás de um reverse proxy com autenticação.
- Parâmetros da API são validados: `symbol`/`timeframe` restritos aos valores
  do `config.yaml` e limites numéricos com faixas máximas.
- O front escapa todo conteúdo dinâmico antes de renderizar (anti-XSS), e as
  mensagens do Telegram usam `html.escape`.
- Segredos (token do Telegram) ficam no `.env`, que está no `.gitignore`.
- Nenhuma chave de exchange é necessária: o sistema só lê dados públicos e
  nunca envia ordens reais.

## Qualidade

```bash
python -m pytest tests/   # 38 testes
ruff check .              # lint + regras de segurança (flake8-bandit)
```

Os testes cobrem indicadores (valores de referência), níveis/estrutura,
estratégia (cenários sintéticos de tendência), risk manager, ciclo completo do
paper trading (TP1 → breakeven → TP2 → TP3 / stop) e o motor de backtest
(contabilidade, limite de perda por trade e métricas).

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

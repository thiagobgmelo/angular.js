# 03 — Ajustes e correções (histórico com os porquês)

Registro de tudo que foi ajustado durante o desenvolvimento, por iteração,
com a motivação de cada mudança. Serve de memória técnica: se algum
comportamento parecer estranho no futuro, a explicação provavelmente está aqui.

## Iteração 1 — Construção (commit `1e24ebd`)

Ajustes feitos durante a construção, descobertos pelos próprios testes:

### Empates na detecção de fractais de swing
**Sintoma**: `swing_points` não detectava pivôs óbvios nos dados de teste.
**Causa**: o candle seguinte ao pico abre no preço de fechamento anterior,
criando máximas *empatadas* na janela — e a regra exigia máxima estritamente
única.
**Ajuste** (`app/analysis/levels.py`): o pivô é a **primeira ocorrência** do
extremo dentro da janela (`argmax == lookback`), o que trata empates uma única
vez sem perder pivôs legítimos.

### RSI contra a tendência não pontua
**Sintoma**: em tendência de baixa clara, o score LONG empatava com o SHORT
porque "RSI sobrevendido" pontuava a favor de compra.
**Causa**: regra ingênua — em tendência forte, "sobrevendido pode continuar
sobrevendido" (comportamento documentado do RSI).
**Ajuste** (`app/analysis/strategy.py`): extremo de RSI só pontua **a favor da
tendência vigente** (compra em pullback); sinal contra a tendência exige
**divergência** preço×RSI. Além de corrigir o teste, é a prática recomendada
na literatura.

### Seed do gerador demo estável entre processos
**Sintoma**: os dados demo mudavam a cada execução do CLI.
**Causa**: `hash()` do Python é salteado por processo (PYTHONHASHSEED).
**Ajuste**: seed via `zlib.crc32(f"{symbol}|{timeframe}")` — determinístico
sempre.

### Série demo de tamanho fixo
**Sintoma**: gráfico e análise mostravam preços diferentes para o mesmo par.
**Causa**: o gerador criava a série a partir do `limit` pedido — pedidos com
`limit` diferente recebiam finais de série diferentes.
**Ajuste**: série longa fixa (5000 candles) e o `limit` corta apenas o final —
todos os consumidores veem os mesmos dados.

## Iteração 2 — QA e segurança (commit `ed4168c`)

Auditoria completa do código da v1. Achados e correções:

### Segurança

| # | Achado | Correção | Arquivo |
|---|---|---|---|
| S1 | Servidor em `0.0.0.0` com endpoint mutável sem autenticação | Bind default `127.0.0.1`; `HOST` explícito para expor | `app/main.py` |
| S2 | Dados da API interpolados em `innerHTML` (risco XSS) | Helper `esc()` em toda interpolação dinâmica | `dashboard/app.js` |
| S3 | UPDATE SQL montado com f-string sobre chaves de `**fields` | Whitelist `ALLOWED_UPDATE_COLUMNS`; `ValueError` fora dela | `app/paper/store.py` |
| S4 | SQLite compartilhado entre threads sem proteção (FastAPI usa threadpool) | `threading.Lock` em todas as operações do `Store` | `app/paper/store.py` |
| S5 | Parâmetros da API sem validação/limites (exaustão de recursos; símbolos arbitrários repassados à exchange) | `Query(ge/le)` nos numéricos; `symbol`/`timeframe` restritos ao config → 422 | `app/server.py` |
| S6 | Mensagens Telegram com `parse_mode=HTML` sem escape | `html.escape` nos campos dinâmicos | `app/alerts/telegram.py` |

Verificação dinâmica: `limit=999999` → 422; `symbol=<script>` → 422; interface
externa da máquina com a porta fechada (teste de socket real).

### QA / robustez

| # | Achado | Correção |
|---|---|---|
| Q1 | **Bug real**: atualização de posições reprocessava candles (scans frequentes) ou perdia candles (scans espaçados) | Marcador `checked_until` por posição; só candles com timestamp posterior são processados; validado com dois scans consecutivos produzindo estado idêntico |
| Q2 | Closures do backtest capturavam variáveis de loop (`B023` — bug latente clássico) | Helpers `_trade_pnl`/`_hit` com argumentos explícitos |
| Q3 | Config sem validação (risco de 500%, lista de pares vazia passavam) | `validate()` em `app/config.py` com mensagens claras |
| Q4 | Backtest sem testes | `tests/test_backtest.py`: contabilidade fecha ao centavo, perda por trade limitada ao risco, série lateral não opera |
| Q5 | `print` no código de biblioteca; front quebrava com erro não-JSON | `logging` estruturado; `api()` tolerante no front |

### Boas práticas
Ruff com regras de segurança (38 apontamentos iniciais corrigidos → base
limpa), dependências pinadas, seções de Segurança e Qualidade no README.

## Iteração 3 — Tempo real (commit `151f890`)

Motivada pelo apontamento do usuário: latência de dados é crucial para a
qualidade das entradas. Diagnóstico e resultado:

| Caminho | Antes (v1/v2) | Depois (v3, medido) |
|---|---|---|
| Reação ao fechamento de candle | até 300 s (polling fixo) | **0,20 s** (evento websocket) + análise em **55 ms** |
| Stop/alvos das posições paper | próximo scan, granularidade de candle | **tick a tick**, fill no nível exato |
| Dashboard | polling 60 s | **push SSE**; candle corrente se move ao vivo |
| `/api/ohlcv` | 0,5–2 s (REST à exchange a cada request) | **~45 ms** (cache em memória) |
| Idade do dado | não observável | **0,28 s**, exposta em `/api/health` e no badge do dashboard |

Mudanças estruturais (detalhes no [ADR-009](02-decisoes-de-arquitetura.md#adr-009--websocket--arquitetura-orientada-a-eventos-v3)
e no [04 — Funcionamento](04-funcionamento.md)):

- `app/data/feed.py`: `MarketFeed` (websocket), fallback REST alinhado ao
  relógio, `DemoFeed`, cache `CandleCache` com detecção de fechamento,
  watchdog de staleness, throttle de ticks.
- `app/engine.py`: `TradingEngine` orientado a eventos + bus interno para SSE.
- `app/paper/portfolio.py`: `update_with_tick` (persiste apenas quando um
  nível é cruzado — evita escrita no banco a cada tick).
- `app/server.py`: `lifespan` gerencia o feed; endpoints servem do cache;
  `/api/stream` (SSE) e `/api/health`.
- Dashboard: `EventSource`, badge de latência, fallback para polling.
- `CRYPTO_TRADER_CONFIG` para configs alternativos (necessidade surgida nos
  testes e2e; útil para múltiplos perfis).

Ajustes menores durante a implementação:
- `publish` do bus descarta o evento mais antigo quando um assinante SSE está
  lento (nunca bloqueia o caminho quente).
- Screenshot e2e: `wait_until="networkidle"` nunca dispara com SSE aberto
  (conexão permanente) — trocado por `"load"`. Registrado porque afeta
  qualquer automação de browser contra o dashboard.
- Suíte final: **54 testes**; comandos batch (`scan`/`analyze`/`backtest`)
  preservados sobre REST simples.

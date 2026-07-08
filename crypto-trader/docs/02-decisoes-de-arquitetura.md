# 02 — Decisões de arquitetura (ADRs)

Registro das decisões técnicas no formato: contexto → alternativas → decisão →
porquê. A numeração segue a ordem cronológica em que foram tomadas.

---

## ADR-001 — Python + ccxt como base

**Contexto**: escolher a stack para um sistema de análise técnica e trading.

**Alternativas**: Node.js/TypeScript (ccxt também existe em JS); frameworks
prontos como Freqtrade.

**Decisão**: Python com ccxt, pandas e numpy, código próprio.

**Porquê**: Python domina o ecossistema de trading quantitativo — mais
bibliotecas validadas, exemplos e material de referência. Um framework pronto
(Freqtrade) esconderia exatamente o que o usuário queria ver: as teorias e os
critérios de decisão explícitos. O ccxt é a biblioteca de integração com
exchanges mais usada do mundo (100+ exchanges na mesma interface).

---

## ADR-002 — Binance como fonte de dados, trocável por configuração

**Contexto**: de onde vêm os candles (OHLCV).

**Alternativas**: múltiplas exchanges desde o início; agregadores (CoinGecko).

**Decisão**: Binance por padrão (`exchange.id` no `config.yaml`), abstraída
pelo ccxt.

**Porquê**: maior liquidez do mundo e API pública de candles **sem chave e sem
conta**. Como todo acesso passa pelo ccxt, trocar para Bybit/OKX/Coinbase é
mudar uma linha de config — suporte multi-exchange de fato, sem a complexidade
de gerenciar N conexões na v1. Agregadores têm granularidade e latência piores
que a exchange de origem.

---

## ADR-003 — Indicadores implementados em pandas, não TA-Lib/pandas-ta

**Contexto**: RSI, MACD, EMA, ATR, Bollinger precisam ser calculados.

**Alternativas**: TA-Lib (binding C), pandas-ta, biblioteca `ta`.

**Decisão**: módulo próprio (`app/analysis/indicators.py`) com as fórmulas
clássicas (Wilder para RSI/ATR, Appel para MACD) em pandas/numpy puros.

**Porquê**: TA-Lib exige compilação de binário C (fricção de instalação);
pandas-ta tem incompatibilidades conhecidas com numpy 2.x. As fórmulas são
padrão da literatura e cabem em ~90 linhas testáveis contra valores de
referência (`tests/test_indicators.py`) — os resultados batem com o
TradingView, que usa as mesmas suavizações (RMA de Wilder).

---

## ADR-004 — TradingView entra via Lightweight Charts

**Contexto**: o usuário perguntou se o TradingView faria parte do sistema.

**Alternativas**: dados do TradingView (não existe API pública/gratuita de
dados; scraping viola termos de uso); widgets embed do TradingView (carregam
conteúdo externo e não aceitam dados próprios no plano gratuito); bibliotecas
de gráfico genéricas (Chart.js, ECharts).

**Decisão**: **Lightweight Charts**, a biblioteca open-source oficial do
TradingView, vendorizada em `dashboard/vendor/` (versão 4.2.3).

**Porquê**: entrega exatamente a aparência e UX de gráfico financeiro que
traders reconhecem, aceita nossos dados (da Binance), é open-source, leve
(~160 KB) e funciona offline por estar vendorizada. É a forma legítima de "ter
o TradingView" no produto.

---

## ADR-005 — Decisão em candle fechado + confluência com dominância

**Contexto**: quando e como a estratégia decide emitir um sinal.

**Alternativas**: analisar o candle em formação (reação mais rápida); um único
indicador-gatilho; score sem exigência de dominância.

**Decisão**: a análise roda **somente sobre candles fechados**
(`df.iloc[:-1]` em todos os caminhos), com motor de confluência de 7 critérios
que exige score mínimo (default 4/7) **e** dominância de ≥2 pontos sobre o
lado oposto, **e** R:R ≥ 1,5 até o TP1.

**Porquê**: indicador calculado sobre candle em formação muda até o
fechamento ("repaint") — sinais baseados nele são irreproduzíveis e enganosos
no backtest. A dominância evita operar mercado indeciso (ex.: LONG 4 × SHORT
3 não gera sinal). O filtro de R:R descarta sinais tecnicamente válidos mas
economicamente ruins. A velocidade vem de *reagir rápido ao fechamento*
(ADR-009), não de decidir antes dele.

---

## ADR-006 — Paper trading com regras conservadoras

**Contexto**: como simular a execução com realismo suficiente para os
resultados terem valor preditivo.

**Decisão**:
- Gestão em escada: TP1 (1,5R) realiza 50% e move o stop para breakeven;
  TP2 (2,5R) realiza 25%; TP3 (próxima zona de S/R) fecha o restante.
- Com granularidade de candle (backtest/backfill): se stop e alvo saem no
  mesmo candle, **assume-se que o stop veio primeiro**.
- Com ticks (modo ao vivo): fill no **nível exato** do stop/alvo, não no preço
  do tick que cruzou.

**Porquê**: simulações otimistas produzem estratégias que só funcionam no
papel. O viés conservador faz o resultado simulado ser um piso, não um teto.
O fill no nível exato evita superestimar ganhos quando um tick salta vários
níveis.

---

## ADR-007 — SQLite como persistência

**Contexto**: sinais, posições e curva de equity precisam sobreviver a
restarts.

**Alternativas**: PostgreSQL, arquivos JSON, apenas memória.

**Decisão**: SQLite (stdlib) com uma conexão protegida por `threading.Lock` e
whitelist de colunas nos UPDATEs.

**Porquê**: sistema single-user local — um servidor de banco seria peso morto
operacional. SQLite é confiável, zero-config e suficiente para o volume (um
INSERT por sinal/evento). O lock existe porque o FastAPI executa endpoints
sync em threadpool (detalhe descoberto na auditoria — ver
[03 — Ajustes](03-ajustes-e-correcoes.md)).

---

## ADR-008 — Bind em localhost, sem autenticação na v1

**Contexto**: a API tem endpoint mutável (`POST /api/scan`) e nenhum
mecanismo de login.

**Alternativas**: implementar token/JWT já na v1; deixar em `0.0.0.0`.

**Decisão**: servidor escuta apenas em `127.0.0.1` por padrão; expor exige
`HOST=0.0.0.0` explícito e está documentado como decisão consciente do
operador (recomendação: reverse proxy com autenticação).

**Porquê**: para uso pessoal local, o bind em loopback **é** a autenticação —
só processos da própria máquina alcançam a porta. Um sistema de login na v1
adicionaria superfície de ataque (gestão de credenciais) sem ganho real no
cenário de uso. A decisão está registrada no README para o dia em que o
cenário mudar.

---

## ADR-009 — Websocket + arquitetura orientada a eventos (v3)

**Contexto**: a v1 usava polling fixo de 300 s — até 5 minutos entre o
fechamento de um candle e a análise, inaceitável para trading (apontado pelo
usuário).

**Alternativas**: (a) só encurtar o intervalo de polling (desperdiça rate
limit e ainda chega atrasado); (b) polling alinhado ao relógio dos candles;
(c) websocket com streaming contínuo.

**Decisão**: websocket via **ccxt.pro** (incluído gratuitamente no ccxt 4.x —
verificado na versão instalada) como modo principal: `watch_ohlcv` +
`watch_ticker` alimentam um cache em memória (`MarketFeed`) que emite eventos
de *candle fechado* e de *tick*. A alternativa (b) foi implementada como
**fallback** (`feed.mode: rest`): fetch 2 s após cada fechamento teórico.

**Porquê**: a exchange empurra o dado no momento em que existe — é o menor
tempo de reação fisicamente possível para um cliente. Medições na verificação:
evento de candle fechado 0,20 s após o fechamento teórico, análise concluída
em 55 ms, dados servidos do cache em ~45 ms. Resiliência: reconexão com
backoff, watchdog de staleness que reinicia streams parados, e o fallback REST
alinhado que mantém o sistema útil mesmo sem websocket.

---

## ADR-010 — SSE (não WebSocket) entre servidor e dashboard

**Contexto**: o dashboard precisa receber ticks/candles/sinais em tempo real.

**Alternativas**: WebSocket bidirecional; polling curto.

**Decisão**: Server-Sent Events (`GET /api/stream`), com fallback automático
para o polling antigo se o stream cair.

**Porquê**: o fluxo é estritamente unidirecional (servidor → navegador) — as
ações do usuário já são requests REST normais. SSE roda sobre HTTP simples,
o `EventSource` do navegador **reconecta sozinho** (com WebSocket teríamos de
implementar isso à mão) e o servidor não precisa de handshake/upgrade. Menos
código para o mesmo resultado.

---

## ADR-011 — Provedor demo determinístico

**Contexto**: o ambiente de desenvolvimento remoto bloqueia rede para
exchanges; e usuários novos querem ver o sistema funcionando sem depender de
internet.

**Decisão**: `EXCHANGE_ID=demo` ativa `DemoClient` (REST sintético) e
`DemoFeed` (ticks sintéticos ~0,5 s fechando candles no relógio real), com a
mesma interface dos provedores reais. Séries determinísticas: seed `crc32`
do par+timeframe sobre uma série longa de tamanho fixo.

**Porquê**: permitiu verificar 100% do pipeline (análise → sinal → posição →
ticks → SSE → dashboard) neste ambiente, e dá onboarding offline. O
determinismo importa: seeds estáveis entre processos e séries de tamanho fixo
garantem que gráfico, análise e backtest vejam os mesmos dados (dois bugs
reais dessa natureza foram corrigidos — ver [03](03-ajustes-e-correcoes.md)).

---

## ADR-012 — Qualidade automatizada: ruff (com bandit) + dependências pinadas

**Contexto**: pedido explícito de boas práticas, QA e segurança na iteração 2.

**Decisão**: `ruff` no `pyproject.toml` com regras `E,F,W,I,UP,B,S` — a
família `S` é o flake8-bandit (análise estática de segurança). `requirements.txt`
congela as versões exatas testadas; `requirements.in` mantém os ranges.

**Porquê**: lint com regras de segurança acha classes inteiras de problemas
de graça (o `B023` pegou um bug real de closure no backtest). Pinar versões
torna a instalação reproduzível — num sistema financeiro, "funciona com a
versão que baixou hoje" não é aceitável. Duas exceções documentadas no
próprio config: `S311` (RNG não-criptográfico é correto para dados demo) e
`S101` em testes (assert é a ferramenta do pytest).

---

## ADR-013 — Screener de universo com critérios objetivos (v4)

**Contexto**: o usuário pediu monitoramento paralelo de "majors + altcoins
confiáveis com critérios claros e validados". Confiança precisa ser
operacionalizada em regras verificáveis, não numa lista subjetiva.

**Alternativas**: lista manual curada (subjetiva, envelhece); todos os pares
da exchange (centenas de streams, cheio de lixo ilíquido); fontes externas de
"rating" de projetos (opacas, pagas, mais uma dependência).

**Decisão**: screener automático (`app/screener.py`) sobre dados da própria
exchange, com critérios transparentes e configuráveis: spot USDT ativo,
**volume 24h ≥ US$ 20M** (liquidez), **histórico ≥ 180 dias** de candles 1d
(maturidade — exclui listagens recentes), exclusão estrutural de stablecoins/
fiat e tokens alavancados, ranking por volume com corte em **25 pares**, e
`always_include` fixando as majors (BTC, ETH, SOL, BNB). Universo re-avaliado
a cada 6 h com regra de proteção: **par com posição aberta nunca é removido**
do monitoramento. `enabled: false` volta à lista estática.

**Porquê**: volume e maturidade são os proxies de confiança *mensuráveis* —
moeda ilíquida tem spread ruim e é manipulável; listagem recente não tem
gráfico para analisar (nem EMA200 diária). Os critérios ficam no config, à
vista, ajustáveis e auditáveis: exatamente o que "critérios claros e
validados" significa na prática. A filtragem é uma função pura
(`select_universe`) testada com payloads realistas da exchange.

---

## ADR-014 — Alavancagem sugerida derivada do stop (v5)

**Contexto**: o usuário quer operar perpétuos USDT na Bybit com alavancagem
"efetiva e eficiente" e entender o porquê de cada sugestão.

**Alternativas**: alavancagem fixa escolhida pelo usuário (ignora que cada
setup tem stop diferente); alavancagem máxima da exchange (liquidação vira o
stop de fato — inaceitável); dimensionar risco *pela* alavancagem (erro
conceitual comum que transforma alavancagem em risco).

**Decisão**: o risco por trade continua fixo (1% via distância do stop). A
alavancagem sugerida é **a maior que mantém o preço de liquidação a pelo
menos `liq_buffer` (3×) a distância do stop**, com teto `max_leverage` (10x,
escolhido pelo usuário): `alav = min(⌊1/(3×dist_stop + mmr)⌋, 10)`, onde
`mmr` ≈ 0,5% é a margem de manutenção. Cada sinal carrega alavancagem,
margem imobilizada, liquidação estimada e o racional em texto.

**Porquê**: com sizing por stop, alavancagem não muda o quanto se perde no
stop — muda quanta margem fica presa e onde cai a liquidação. "Eficiente" é
imobilizar o mínimo de margem; "efetivo e seguro" é garantir que a liquidação
nunca aconteça antes do stop (por isso o buffer de 3× — nem um pavio violento
que estoure o stop chega perto da liquidação). O racional em cada sinal torna
a regra auditável. Riscos reais registrados: liquidação (mitigada pelo
buffer), funding em posições longas no tempo (documentado como custo).

---

## ADR-015 — Autenticação por token + deploy Docker/Caddy (v5)

**Contexto**: o sistema sai do localhost para "uso e acesso" remoto.

**Alternativas**: login com usuário/senha e sessões (peso desnecessário para
single-user); HTTP básico (sem logout/rotação limpa); OAuth (exagero).

**Decisão**: token único (`API_TOKEN` env) exigido em `/api/*` via middleware
com `secrets.compare_digest`; SSE autentica por query param (EventSource não
envia headers). Sem a env definida, comportamento dev local (bind 127.0.0.1
como proteção). Empacotamento: Docker (imagem non-root com healthcheck) +
docker-compose com volume para o SQLite + Caddy para HTTPS automático;
runbook em docs/05 com opções de custo zero (Oracle Always Free / Tailscale).

**Porquê**: para um usuário único, um bearer token forte sobre HTTPS dá a
mesma proteção prática que um sistema de login, com uma fração da superfície
de ataque. Caddy elimina a gestão manual de certificados. O compose isola o
app (sem porta pública direta — só o Caddy fala com ele).

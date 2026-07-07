# Documentação do Crypto Trader

Registro completo do projeto: planejamento, decisões (com os porquês), ajustes
feitos ao longo do desenvolvimento e o funcionamento da plataforma.

## Mapa de leitura

| Documento | O que responde |
|---|---|
| [01 — Visão e planejamento](01-visao-e-planejamento.md) | Por que o projeto existe, o que foi pedido, o que foi decidido no escopo e o que vem depois |
| [02 — Decisões de arquitetura (ADRs)](02-decisoes-de-arquitetura.md) | Por que cada tecnologia/abordagem foi escolhida, e o que foi considerado e descartado |
| [03 — Ajustes e correções](03-ajustes-e-correcoes.md) | O histórico das 3 iterações: cada bug, ajuste e melhoria, com a motivação |
| [04 — Funcionamento da plataforma](04-funcionamento.md) | Como o sistema funciona de ponta a ponta: arquitetura, estratégia, risco, ciclo de vida do trade, operação |

**Leitura recomendada para quem chega agora**: 01 → 04 → 02 → 03.

## Correspondência com o histórico do repositório

| Iteração | Commit | Conteúdo |
|---|---|---|
| v1 — construção | `1e24ebd` | Sistema completo: análise, sinais, paper trading, backtest, dashboard, Telegram |
| v2 — QA + segurança | `ed4168c` | 6 correções de segurança, 5 de robustez, lint com regras de segurança, deps pinadas |
| v3 — tempo real | `151f890` | Websocket + eventos: reação sub-segundo, stops tick a tick, SSE no dashboard |

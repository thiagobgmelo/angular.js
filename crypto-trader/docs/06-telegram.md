# 06 — Bot do Telegram: setup e uso

O bot é o controle remoto do sistema: recebe sinais e avisos, aprova/rejeita
entradas no modo manual e comanda o modo de execução — tudo do celular.

## Setup (5 minutos, direto no app do Telegram)

1. Fale com **@BotFather** → `/newbot` → escolha nome e username (termina em
   `bot`) → copie o **token**
2. Fale com **@userinfobot** → `/start` → copie o seu **Id** numérico
3. Abra o chat do SEU bot recém-criado e envie `/start` (sem isso ele não
   pode te mandar mensagens)
4. No servidor, preencha o `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAF...
   TELEGRAM_CHAT_ID=123456789
   ```
5. Valide: `python -m app.main telegram-test` → deve chegar "✅ Crypto Trader
   conectado" no seu chat

## Segurança

- O bot **só obedece ao chat configurado** em `TELEGRAM_CHAT_ID` — comandos e
  botões vindos de qualquer outro chat são ignorados e logados.
- O token é segredo: só no `.env` (nunca no repositório). Se vazar, revogue no
  BotFather (`/revoke`).

## Comandos

| Comando | Efeito |
|---|---|
| `/status` | Equity, PnL, posições, modo de execução, breaker, universo |
| `/posicoes` | Posições abertas com níveis |
| `/radar` | Últimas oportunidades em formação |
| `/modo off\|manual\|auto` | Troca o modo de execução em runtime |
| `/pausar` | Kill-switch: pausa a execução imediatamente |
| `/retomar` | Retoma após pausa (manual ou por circuit breaker) |
| `/ajuda` | Lista de comandos |

## Aprovação de entradas (modo manual)

Quando um sinal sai com `/modo manual` ativo, chega uma mensagem com o resumo
(direção, par, alavancagem, entrada/stop/alvos) e dois botões:

> 🔔 **Aprovar entrada?** (expira em 15 min)
> 🟢 LONG BTC/USDT 4h · 5x
> Entrada 64.210 · Stop 62.100 · Alvos 67.400 / 69.500 / 72.800
> [ ✅ Aprovar ] [ ❌ Rejeitar ]

- **Aprovar** → a ordem sai na hora (com TP1/TP2/stop já posicionados)
- **Rejeitar** ou deixar expirar → nada acontece (o paper trading registra
  normalmente para comparação)
- A validade é `execution.approval_ttl_min` (default 15 min) — sinal velho é
  sinal vencido

## Avisos automáticos

- **Sinal completo**: com alavancagem sugerida, margem e liquidação estimada
- **Circuit breaker acionado**: qual limite estourou e que a execução foi
  pausada (retome com `/retomar` após revisar)
- **Erros de ordem consecutivos**: pausa automática + aviso

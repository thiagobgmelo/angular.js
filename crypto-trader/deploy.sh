#!/usr/bin/env bash
# Bootstrap de deploy do Crypto Trader numa VPS Ubuntu/Debian recém-criada.
#
# Uso (na VPS, como usuário com sudo):
#   git clone <SEU_FORK> app && cd app/crypto-trader && bash deploy.sh
#
# O script é idempotente: rodar de novo atualiza a instalação sem perder dados.
set -euo pipefail

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m✘ %s\033[0m\n' "$*"; exit 1; }

cd "$(dirname "$0")"
[ -f docker-compose.yml ] || fail "rode este script de dentro da pasta crypto-trader/"

# --- 1. Docker -------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  say "Instalando Docker (script oficial)..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
  ok "Docker instalado"
else
  ok "Docker já presente: $(docker --version)"
fi

# --- 2. Firewall (se ufw existir) -------------------------------------------
if command -v ufw >/dev/null 2>&1; then
  say "Configurando firewall (22/80/443)..."
  sudo ufw allow 22/tcp >/dev/null && sudo ufw allow 80/tcp >/dev/null \
    && sudo ufw allow 443/tcp >/dev/null
  sudo ufw --force enable >/dev/null
  ok "UFW ativo com 22/80/443"
fi

# Oracle Cloud: as imagens Ubuntu vêm com regras REJECT no iptables que
# bloqueiam 80/443 mesmo com o Security List liberado no painel
if sudo iptables -L INPUT -n 2>/dev/null | grep -q "REJECT"; then
  say "Firewall iptables da imagem detectado (padrão Oracle) — liberando 80/443..."
  sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save 2>/dev/null || true
  ok "iptables liberado para 80/443"
fi

# --- 3. .env interativo ------------------------------------------------------
if [ ! -f .env ]; then
  say "Configurando o .env (Enter aceita o padrão)..."
  cp .env.example .env

  TOKEN=$(openssl rand -hex 32)
  sed -i "s|^API_TOKEN=.*|API_TOKEN=${TOKEN}|" .env
  ok "API_TOKEN gerado (será exibido no final — guarde-o)"

  read -rp "Domínio para HTTPS (vazio = sem domínio/uso via IP ou Tailscale): " DOMAIN
  if [ -n "${DOMAIN}" ]; then
    sed -i "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" .env
    ok "Caddy emitirá HTTPS para ${DOMAIN} (DNS deve apontar para este IP)"
  fi

  read -rp "Token do bot do Telegram (vazio = pular): " TG_TOKEN
  if [ -n "${TG_TOKEN}" ]; then
    read -rp "Seu chat id do Telegram: " TG_CHAT
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${TG_TOKEN}|" .env
    sed -i "s|^TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=${TG_CHAT}|" .env
    ok "Telegram configurado"
  fi

  read -rp "API key da Bybit TESTNET (vazio = executor dry-run): " BB_KEY
  if [ -n "${BB_KEY}" ]; then
    read -rp "API secret da Bybit TESTNET: " BB_SECRET
    sed -i "s|^BYBIT_API_KEY=.*|BYBIT_API_KEY=${BB_KEY}|" .env
    sed -i "s|^BYBIT_API_SECRET=.*|BYBIT_API_SECRET=${BB_SECRET}|" .env
    sed -i "s|^# EXCHANGE_ID=.*|EXCHANGE_ID=bybit|" .env
    ok "Bybit testnet configurada (BYBIT_TESTNET=1 permanece ligado)"
  fi
  chmod 600 .env
else
  ok ".env já existe — mantido (edite manualmente se precisar)"
fi

# --- 4. Subir ----------------------------------------------------------------
say "Construindo e subindo (primeira vez leva alguns minutos)..."
sudo docker compose up -d --build

say "Aguardando o feed aquecer (~60s)..."
TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2)
for i in $(seq 1 30); do
  sleep 5
  CODE=$(sudo docker compose exec -T app python -c "
import os, urllib.request
t = os.environ.get('API_TOKEN', '')
u = 'http://127.0.0.1:8000/api/health' + (f'?token={t}' if t else '')
try:
    urllib.request.urlopen(u, timeout=3); print('200')
except Exception:
    print('erro')" 2>/dev/null || echo erro)
  [ "$CODE" = "200" ] && break
done
[ "$CODE" = "200" ] || fail "app não respondeu — veja: sudo docker compose logs app"

# --- 5. Resumo ---------------------------------------------------------------
IP=$(curl -fsS -m 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
DOMAIN=$(grep '^DOMAIN=' .env | cut -d= -f2)
say "PRONTO! Sistema no ar."
echo "─────────────────────────────────────────────────────────"
if [ -n "${DOMAIN}" ] && [ "${DOMAIN}" != "localhost" ]; then
  echo "  Dashboard : https://${DOMAIN}"
else
  echo "  Dashboard : http://${IP} (via Caddy) — configure DOMAIN p/ HTTPS válido"
fi
echo "  Token     : ${TOKEN}"
echo "  (o dashboard pede este token no primeiro acesso)"
echo "─────────────────────────────────────────────────────────"
echo "  Validar Telegram : sudo docker compose exec app python -m app.main telegram-test"
echo "  Validar Bybit    : sudo docker compose exec app python -m app.main exec-test"
echo "  Logs             : sudo docker compose logs -f app"
echo "  Atualizar        : git pull && sudo docker compose up -d --build"

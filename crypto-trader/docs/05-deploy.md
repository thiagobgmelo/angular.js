# 05 — Deploy: subindo o sistema para uso e acesso

Runbook passo a passo para colocar o Crypto Trader no ar com HTTPS,
autenticação e backup. Os artefatos (`Dockerfile`, `docker-compose.yml`,
`Caddyfile`) já estão no repositório.

## Passo 0 — Escolher onde hospedar

| Opção | Custo | Prós | Contras |
|---|---|---|---|
| **Oracle Cloud Always Free** | **R$ 0** | VPS de verdade (ARM até 4 vCPU/24 GB), gratuita para sempre | Exige cartão no cadastro; disponibilidade de instância ARM varia por região (tente São Paulo/Vinhedo e alternativas); recuperação de instância pode ser burocrática |
| **Máquina própria 24/7 + Tailscale** | R$ 0 (energia) | Sem cadastro em nuvem; acesso remoto seguro **sem expor porta na internet** | Depende da sua máquina/energia/internet ficarem no ar |
| VPS paga (Hetzner, DigitalOcean, Vultr...) | ~US$ 4–6/mês | Confiabilidade e simplicidade máximas | Custo mensal |

**Custo zero com eficiência e segurança?** Sim: a Oracle Always Free roda o
sistema com folga enorme (o app usa < 1 vCPU e ~300 MB de RAM). A segurança
independe do preço — vem do hardening abaixo, do token e do HTTPS. A única
concessão do gratuito é operacional (disponibilidade/burocracia da Oracle).
Se a Oracle não tiver instância na sua região, a via Tailscale é igualmente
segura (nada exposto) e o dashboard fica acessível de qualquer dispositivo
seu logado na tailnet.

## Passo 1 — Preparar o servidor (Ubuntu 22.04+)

```bash
# como root, crie um usuário e desabilite login root por senha
adduser trader && usermod -aG sudo trader

# firewall: só SSH e HTTPS
sudo apt update && sudo apt install -y ufw fail2ban
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable

# Docker (script oficial)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker trader   # relogue depois disso
```

> Na opção Tailscale (sem exposição): instale `tailscale up` e **não** abra
> 80/443 no firewall — acesse via IP da tailnet; o Caddy é dispensável
> (descomente o `ports: 127.0.0.1:8000` do compose e use o app direto).

## Passo 2 — Instalar o sistema

```bash
git clone <seu-fork> && cd crypto-trader
cp .env.example .env
openssl rand -hex 32        # cole o resultado em API_TOKEN no .env
nano .env                   # DOMAIN=trader.seudominio.com, TELEGRAM_*, EXCHANGE_ID
```

Aponte o DNS do seu domínio (registro A) para o IP do servidor **antes** de
subir — o Caddy emite o certificado Let's Encrypt automaticamente no primeiro
acesso.

## Passo 3 — Subir

```bash
docker compose up -d --build
docker compose logs -f app      # acompanhe o seed do feed (~1 min)
```

Verificações:

```bash
TOKEN=$(grep ^API_TOKEN .env | cut -d= -f2)
curl -s -o /dev/null -w "%{http_code}\n" https://SEU_DOMINIO/api/health                     # 401
curl -s -H "Authorization: Bearer $TOKEN" https://SEU_DOMINIO/api/health | head -c 200      # 200
```

No navegador: `https://SEU_DOMINIO` → o dashboard pede o token uma única vez.

## Passo 4 — Backup do banco (sinais, radar, trades)

```bash
crontab -e   # backup diário às 03:00, mantém 14 dias
0 3 * * * docker compose -f /home/trader/crypto-trader/docker-compose.yml exec -T app \
  sh -c 'cp /data/paper_trading.db /data/backup-$(date +\%F).db' && \
  docker compose -f /home/trader/crypto-trader/docker-compose.yml exec -T app \
  sh -c 'ls -t /data/backup-*.db | tail -n +15 | xargs -r rm'
```

## Passo 5 — Atualizar o sistema

```bash
cd crypto-trader && git pull
docker compose up -d --build    # rebuild + restart sem perder o volume de dados
```

## Checklist de segurança do deploy

- [ ] `API_TOKEN` forte no `.env` (nunca comitado — está no `.gitignore`)
- [ ] Firewall só com 22/80/443 (ou nada exposto, na via Tailscale)
- [ ] HTTPS ativo (Caddy) — token nunca trafega em claro
- [ ] Backup diário testado (restaure um backup uma vez para validar)
- [ ] (Futuro, fase de execução real) chaves Bybit **sem permissão de saque**
      e com **IP whitelist** do servidor; nunca no repositório

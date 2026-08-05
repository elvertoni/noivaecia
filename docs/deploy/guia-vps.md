# Guia de deploy — VPS Ubuntu + Docker Swarm + Traefik

> Guia operacional do Noivas & Cia em produção.
> Especificação e justificativas: [`PRD-deploy.md`](PRD-deploy.md).
> Arquitetura espelhada do projeto de referência `C:\PROJETOS\scsi_v1`.

Convenções deste guia:

- Blocos marcados **✅ JÁ EXECUTADO** já foram aplicados na VPS `169.58.79.15`; ficam
  documentados para reprodutibilidade em outro servidor.
- Placeholders entre `<>` — substituir pelo valor real. **Nenhum segredo real aqui.**
- Comandos rodam como usuário `deploy` (não-root, nos grupos `sudo` e `docker`).
- O endpoint de saúde é **`/healthz/`**. O SCSI usa `/health/`; onde a referência disser
  `/health/`, aqui é `/healthz/`.
- **Nenhum painel de gerenciamento nesta VPS.** Sem EasyPanel, sem Portainer. As menções
  a EasyPanel se referem sempre à VPS antiga, que segue no ar como origem dos dados até
  o cutover (seção 10).

| Parâmetro | Valor |
|---|---|
| VPS | `169.58.79.15` — Ubuntu 24.04, 4 vCPU / 8 GB |
| `DOMAIN` (validação) | `novo.tonicoimbra.com` |
| `DOMAIN` (produção) | **a definir** — cliente escolhendo |
| Registry | `ghcr.io/elvertoni/noivaecia` |
| Stack | `noivaecia` (arquivo único `docker-stack.yml`, Traefik incluso) |

> **Uma variável governa o domínio.** `DOMAIN` define o host do app
> (`Host(\`${DOMAIN}\`)`), o host do dashboard (`traefik.${DOMAIN}`) e o certificado
> wildcard (`${DOMAIN}` + `*.${DOMAIN}`). Nada é hardcoded.
>
> Enquanto a cliente decide, use `novo.tonicoimbra.com` — zona que você já controla no
> Cloudflare. **Tudo é validável agora**, inclusive a emissão do wildcard por DNS-01. Só
> a seção 10 fica bloqueada.

---

## 0. Atalho: `setup_deploy.sh`

O SCSI tem um script que faz o servidor inteiro do zero — sistema, usuário `deploy`,
Docker, Swarm, chave do GitHub, clone, GHCR, `.env`, redes, build, secret do Cloudflare,
deploy e verificação — em duas fases, de forma **idempotente**.

Depois que a S7 do PRD estiver concluída, numa VPS limpa isto substitui as seções 1 a 6:

```bash
# como root, na VPS nova
bash setup_deploy.sh
```

Nesta VPS as seções 1 e 2 já foram feitas à mão, então siga o guia a partir da 1.3
(correções pendentes). O script fica como caminho para o próximo servidor.

---

## 1. Provisionar a VPS

### 1.1 Usuário não-root — ✅ JÁ EXECUTADO

```bash
adduser deploy
usermod -aG sudo deploy

mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

### 1.2 Atualização e utilitários — ✅ JÁ EXECUTADO

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git htop iotop net-tools unzip apache2-utils
sudo timedatectl set-timezone America/Sao_Paulo
```

`apache2-utils` fornece o `htpasswd`, usado na seção 5.2.

### 1.3 Hardening do SSH — ⚠️ PENDENTE (correção)

> **Atenção — erro comum.** As diretivas de servidor (`PermitRootLogin`,
> `PasswordAuthentication`, `MaxAuthTries`) só têm efeito em **`sshd_config`**.
> Colocá-las em `/etc/ssh/ssh_config` (configuração do *cliente*) não protege nada — o
> login root por senha continua aberto.

**Antes de aplicar, abra uma segunda sessão SSH por chave e mantenha-a conectada.** Se
`PasswordAuthentication no` entrar sem uma chave funcionando, você perde o acesso ao
servidor e só o console de recuperação da Contabo resolve.

```bash
# Estado atual (deve mostrar 'yes' nos dois — é o problema)
sudo sshd -T | grep -E 'permitrootlogin|passwordauthentication'

# Limpar o que foi colado no arquivo errado
sudo nano /etc/ssh/ssh_config    # remover as linhas de hardening

# Aplicar no arquivo certo
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

sudo sshd -t          # valida sintaxe. Se der erro, PARE e corrija — não reinicie.
sudo systemctl restart ssh
sudo sshd -T | grep -E 'permitrootlogin|passwordauthentication'   # agora: no / no
```

Abra uma **terceira** sessão. Só feche as antigas depois que ela conectar.

### 1.4 Fail2ban — ⚠️ PENDENTE (correção)

> **Atenção.** No Ubuntu 24.04 a jail do SSH chama-se **`sshd`**, não `ssh`, e precisa
> de `backend = systemd` (`/var/log/auth.log` pode não existir). Um bloco `[ssh]` em
> `jail.local` é silenciosamente ignorado — a jail `sshd` roda mesmo assim, mas com os
> defaults da distribuição (`maxretry=5`, `bantime=10m`), não com os valores escritos.
>
> O `setup_deploy.sh` do SCSI tem esse mesmo defeito (`setup_deploy.sh:282-291`) —
> corrigir no porte.

```bash
sudo apt install -y fail2ban

sudo tee /etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled  = true
backend  = systemd
port     = 22
maxretry = 3
bantime  = 3600
findtime = 600
EOF

sudo systemctl enable fail2ban
sudo systemctl restart fail2ban

sudo fail2ban-client get sshd maxretry   # esperado: 3
sudo fail2ban-client get sshd bantime    # esperado: 3600
```

### 1.5 Swap — ✅ JÁ EXECUTADO

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

### 1.6 Firewall — ✅ JÁ EXECUTADO

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp  comment 'SSH'
sudo ufw allow 80/tcp  comment 'HTTP'
sudo ufw allow 443/tcp comment 'HTTPS'
sudo ufw enable
sudo ufw status verbose
```

Nenhuma outra porta é publicada. Postgres, Evolution API e o app só existem nas redes
overlay internas.

### 1.7 Tuning de kernel — ✅ JÁ EXECUTADO

```bash
sudo tee -a /etc/sysctl.conf <<'EOF'

# === Production Tuning ===
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
fs.file-max = 2097152
fs.inotify.max_user_watches = 524288
vm.swappiness = 10
vm.overcommit_memory = 1
EOF

sudo sysctl -p
```

### 1.8 Docker Engine — ✅ JÁ EXECUTADO

```bash
sudo apt remove -y docker docker-engine docker.io containerd runc 2>/dev/null
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
newgrp docker
sudo systemctl enable --now docker
docker --version
```

### 1.9 Docker para produção — ✅ JÁ EXECUTADO

```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "storage-driver": "overlay2",
  "default-ulimits": {
    "nofile": { "Name": "nofile", "Hard": 65536, "Soft": 65536 }
  },
  "metrics-addr": "127.0.0.1:9323",
  "ipv6": false
}
EOF

sudo systemctl restart docker
```

A rotação de logs (`20m` × 5 por container) é o que sustenta adiar a stack de
observabilidade — sem ela, o disco enche.

---

## 2. Swarm e redes

### 2.1 Swarm e labels — ✅ JÁ EXECUTADO

```bash
docker swarm init --advertise-addr 169.58.79.15

HOSTNAME=$(docker node ls --format '{{.Hostname}}')
docker node update --label-add infra=true $HOSTNAME
docker node update --label-add app=true   $HOSTNAME
docker node inspect --pretty $HOSTNAME | grep -A5 Labels
```

Node único hoje; as labels permitem, ao adicionar um segundo node, fixar banco e app em
máquinas distintas sem reescrever o stack file.

### 2.2 Rede pública compartilhada — ⏳ PENDENTE

```bash
docker network create --driver overlay --attachable traefik_public
docker network ls | grep traefik_public
```

Criada **fora** do stack e declarada `external: true` nele — sobrevive a
`docker stack rm` e pode ser compartilhada por outros projetos no mesmo Traefik.

As redes `noivascia_internal` (isolada, sem saída para a internet) e `noivascia_egress`
(com saída) são criadas pelo próprio stack. Nenhum comando manual.

### 2.3 Auditar o que já está feito

```bash
docker info --format 'swarm={{.Swarm.LocalNodeState}} manager={{.Swarm.ControlAvailable}} driver={{.Driver}}'
docker node ls
free -h | grep -i Swap
sudo ufw status verbose
timedatectl | grep 'Time zone'
```

Esperado: `swarm=active`, `manager=true`, `driver=overlay2`, swap 4 Gi, ufw ativo com
22/80/443, `America/Sao_Paulo`.

---

## 3. GitHub e registry

### 3.1 Chave SSH para `git pull` — ✅ JÁ EXECUTADO

```bash
ssh-keygen -t ed25519 -C "vps-noivascia" -f ~/.ssh/id_ed25519_github
cat ~/.ssh/id_ed25519_github.pub
```

Cadastrar em **github.com → Settings → SSH and GPG keys → New SSH key**. Depois:

```bash
tee -a ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_github
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
chmod 600 ~/.ssh/config

ssh -T git@github.com   # deve responder com o nome de usuário
```

### 3.2 Clonar o repositório — ⏳ PENDENTE

```bash
git clone git@github.com:elvertoni/noivaecia.git ~/noivaecia
cd ~/noivaecia
```

### 3.3 Login no GHCR — ⏳ PENDENTE

Criar um **token classic** em **github.com → Settings → Developer settings → Tokens
(classic)** com escopos `read:packages`, `write:packages`, `delete:packages`.

```bash
echo '<GHCR_PAT>' | docker login ghcr.io -u elvertoni --password-stdin
```

Isso grava `~/.docker/config.json` — é o que o `--with-registry-auth` distribui para os
nodes no deploy.

---

## 4. DNS e token do Cloudflare

### 4.1 Registros DNS

**Fase de validação** — zona `tonicoimbra.com`:

| Tipo | Nome | Conteúdo | Proxy | TTL |
|---|---|---|---|---|
| A | `novo` | `169.58.79.15` | **DNS only** (cinza) | 300 |

Sem proxy durante a validação: qualquer erro é do Traefik ou do app, não do Cloudflare.

Se quiser o dashboard do Traefik acessível, adicione também `traefik.novo` → mesmo IP.
(Com wildcard `*.novo.tonicoimbra.com` o certificado já cobre; falta só o registro.)

**Fase de produção** — quando o domínio for escolhido: adicionar a zona nova ao
Cloudflare, criar os registros A (`@` e `traefik`) para `169.58.79.15`, e emitir um token
**para essa zona**. Tokens ACME são por zona — o de `tonicoimbra.com` não serve para outra.

> **Se ligar o proxy laranja**, o SSL/TLS da zona precisa estar em **Full (strict)**. Em
> `Flexible` o Cloudflare fala HTTP com o Traefik, o Django recebe
> `X-Forwarded-Proto: http`, `SECURE_SSL_REDIRECT` manda para HTTPS, e o loop se fecha.
> Como o TLS é emitido por DNS-01, o proxy pode ficar ativo sem atrapalhar o certificado.

### 4.2 Token de API

**My Profile → API Tokens → Create Token → Edit zone DNS**:

- Permissions: **Zone → DNS → Edit** e **Zone → Zone → Read**
- Zone Resources: **Include → Specific zone → `<sua zona>`** (na validação:
  `tonicoimbra.com`)

Escopo mínimo — um token global de conta daria acesso a tudo. É obrigatório porque o
**wildcard** só sai por desafio **DNS-01**; HTTP-01 e TLS-ALPN-01 não emitem `*.dominio`.

### 4.3 Criar o Docker Secret

```bash
printf '%s' '<CLOUDFLARE_API_TOKEN>' | docker secret create CLOUDFLARE_DNS_API_TOKEN -
docker secret ls
```

`printf '%s'` e **não** `echo` — o `\n` do echo entra dentro do secret e o Cloudflare
rejeita o token.

O Traefik lê pela convenção nativa
`CF_DNS_API_TOKEN_FILE=/run/secrets/CLOUDFLARE_DNS_API_TOKEN`. O valor nunca aparece em
`docker service inspect` nem no `.env`.

---

## 5. Configuração

### 5.1 Arquivo `.env`

Vive em `~/noivaecia/.env` (raiz do repositório clonado), `chmod 600`, **nunca**
versionado (`.gitignore:5`, `.dockerignore:23-25`).

```bash
cd ~/noivaecia
tee .env >/dev/null <<'EOF'
DJANGO_ENV=production
DJANGO_DEBUG=False

# ── Domínio ── as três linhas abaixo são as ÚNICAS a mudar no cutover.
DOMAIN=novo.tonicoimbra.com
DJANGO_ALLOWED_HOSTS=novo.tonicoimbra.com,.novo.tonicoimbra.com,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://novo.tonicoimbra.com,https://*.novo.tonicoimbra.com

ACME_EMAIL=<seu-email>
TRAEFIK_DASHBOARD_AUTH=

DJANGO_SECRET_KEY=<gerar na 5.2>

DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_TRUST_X_FORWARDED_PROTO=True
DJANGO_USE_X_FORWARDED_HOST=False
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False

POSTGRES_DB=noivas_cia
POSTGRES_USER=noivas
POSTGRES_PASSWORD=<gerar na 5.2>
DATABASE_URL=postgresql://noivas:<mesma-senha>@db:5432/noivas_cia

MEDIA_ROOT=/app/media
MEDIA_URL=/media/
STATIC_ROOT=/app/staticfiles
BACKUP_ROOT=/app/data/backups

USER_CREATOR_EMAILS=<seu-email>

EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=Noivas & Cia <no-reply@tonicoimbra.com>

EVOLUTION_API_URL=http://evolution-api:8080
EVOLUTION_API_KEY=<gerar na 5.2>
EVOLUTION_INSTANCE=noivascia
EOF

chmod 600 .env
```

Cinco pontos que causam falha se alterados sem atenção:

1. **`localhost` e `127.0.0.1` em `DJANGO_ALLOWED_HOSTS` são obrigatórios** — o
   `HEALTHCHECK` do container bate em `http://localhost:8000/healthz/`. Sem eles, a task
   nunca fica `healthy` e o Swarm reinicia em loop.
2. **`CSRF_TRUSTED_ORIGINS` exige o esquema** (`https://`). Sem ele o Django ignora a
   entrada e todo POST retorna 403.
3. **`DJANGO_SECRET_KEY` não pode começar com `django-insecure` nem `changeme`** — o
   `settings.py` recusa subir e levanta `ImproperlyConfigured`.
4. **`DEFAULT_FROM_EMAIL` contém `&`.** Por isso nenhum script carrega este arquivo com
   `source` — o `deploy.sh` usa parser de `KEY=VALUE`. O Docker lê `env_file` direto,
   sem shell, e não tem esse problema.
5. **`DOMAIN` é lida de dois jeitos.** Os serviços recebem o `.env` por `env_file`, mas
   os **labels do Traefik** no stack file são interpolados pelo `docker stack deploy` a
   partir do **ambiente do shell**. O `deploy.sh` exporta o `.env` antes de deployar; em
   deploy manual, ver 6.1.

### 5.2 Gerar os segredos

```bash
cd ~/noivaecia

# Chave do Django
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'

# Senha do Postgres e chave da Evolution
openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24; echo
openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24; echo

# Basic Auth do dashboard do Traefik
htpasswd -nbB admin '<SENHA_DO_DASHBOARD>'
```

Cole a saída do `htpasswd` em `TRAEFIK_DASHBOARD_AUTH` **com um único `$`**, exatamente
como ela sai.

> **Não duplique o `$`.** A regra de escrever `$$` vale para hash colocado
> **literalmente** no YAML, onde o Compose interpolaria. Aqui o hash chega por
> `${TRAEFIK_DASHBOARD_AUTH}` — duplicar quebra a autenticação.

---

## 6. Primeiro deploy

### 6.1 Exportar `DOMAIN` (só em deploy manual)

O `scripts/deploy.sh` já faz isso. Para deployar à mão:

```bash
cd ~/noivaecia
export DOMAIN=$(grep -E '^DOMAIN=' .env | cut -d= -f2-)
export ACME_EMAIL=$(grep -E '^ACME_EMAIL=' .env | cut -d= -f2-)
export TRAEFIK_DASHBOARD_AUTH=$(grep -E '^TRAEFIK_DASHBOARD_AUTH=' .env | cut -d= -f2-)
echo "domínio=$DOMAIN"
```

`grep`+`cut` em vez de `source` — o `.env` tem valores com `&` e `$`.

### 6.2 Deploy

O stack é **um arquivo só**, com o Traefik dentro:

```bash
./scripts/deploy.sh            # git pull + build + push + deploy + rollout
# ou, se o GitHub Actions já publicou a imagem:
./scripts/deploy.sh --skip-build
```

Equivalente manual:

```bash
docker stack deploy -c docker-stack.yml --with-registry-auth noivaecia
watch docker stack services noivaecia
```

Esperado, após ~2 minutos (o TLS pode demorar mais):

```
noivaecia_traefik        replicated   1/1
noivaecia_app            replicated   2/2
noivaecia_scheduler      replicated   1/1
noivaecia_db             replicated   1/1
noivaecia_evolution-api  replicated   1/1
```

Se alguma coluna travar em `0/1` ou `1/2`:

```bash
docker service ps noivaecia_app --no-trunc
docker service logs noivaecia_app --tail 100
```

`--no-trunc` é essencial — a mensagem de erro do Swarm é truncada por padrão e o motivo
real fica escondido.

### 6.3 Verificar a emissão do wildcard

O desafio DNS-01 leva de 30s a alguns minutos: o Traefik cria um TXT `_acme-challenge`,
espera o `delaybeforecheck=10`, valida e remove.

```bash
docker service logs noivaecia_traefik 2>&1 | grep -iE 'acme|certificate|obtain'
```

Sucesso: uma linha `Certificates obtained for domains ["$DOMAIN" "*.$DOMAIN"]`.

Do lado do cliente:

```bash
echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null \
  | openssl x509 -noout -issuer -dates -ext subjectAltName
```

Deve listar `DNS:$DOMAIN` e `DNS:*.$DOMAIN`, emissor Let's Encrypt.

### 6.4 Smoke test

```bash
curl -sI "https://$DOMAIN/healthz/"    # 200
curl -s  "https://$DOMAIN/healthz/"    # {"status": "ok"}
curl -sI "http://$DOMAIN/"             # 301/308 para https
```

Dashboard do Traefik: `https://traefik.$DOMAIN` (pede Basic Auth).

Depois, no navegador: login por e-mail, dashboard, criar locação de teste, gerar o PDF do
contrato, subir e reler um `proof_photo`, registrar um recebimento.

### 6.5 Superusuário

```bash
APP=$(docker ps --filter name=noivaecia_app -q | head -1)
docker exec -it $APP python manage.py createsuperuser
docker exec -it $APP python manage.py ensure_admins
```

---

## 7. Operação do dia a dia

### 7.1 Redeploy

```bash
cd ~/noivaecia
./scripts/deploy.sh                 # ciclo completo
./scripts/deploy.sh --skip-build    # só reconcilia o stack
```

Forçar novo pull sem trocar a tag:

```bash
docker service update --force noivaecia_app
docker service update --force noivaecia_scheduler
```

Fixar uma imagem específica, ou voltar à anterior:

```bash
docker service update --image ghcr.io/elvertoni/noivaecia:<sha> noivaecia_app
docker service rollback noivaecia_app
```

### 7.2 Logs

```bash
docker service logs -f noivaecia_app --tail 100
docker service logs -f noivaecia_scheduler --tail 50
docker service logs -f noivaecia_traefik            # emissão de TLS, roteamento
docker service logs noivaecia_db --since 10m
```

### 7.3 Comandos Django

O Swarm não tem `exec` de serviço — resolva a task para um container:

```bash
APP=$(docker ps --filter name=noivaecia_app -q | head -1)

docker exec -it $APP python manage.py showmigrations
docker exec -it $APP python manage.py send_daily_whatsapp_report --dry-run --check
docker exec -it $APP python manage.py wait_for_db --timeout 5
```

### 7.4 Escalar

```bash
docker service scale noivaecia_app=3
```

> **Nunca escale `noivaecia_scheduler` acima de 1.** Duas instâncias do loop enviariam o
> relatório diário duplicado ao cliente. A guarda por `AuditLog` reduz a janela, mas não
> elimina a corrida.

Para desligar o relatório temporariamente: `docker service scale noivaecia_scheduler=0`.

### 7.5 Recursos

```bash
docker stats --no-stream
docker stack services noivaecia
free -h
df -h
```

---

## 8. Troubleshooting

### `DisallowedHost` — o container nunca fica `healthy`

**Sintoma:** task reiniciando; nos logs, `Invalid HTTP_HOST header: 'localhost:8000'`.

**Causa:** `localhost`/`127.0.0.1` ausentes de `DJANGO_ALLOWED_HOSTS`. O `HEALTHCHECK`
do container bate em `http://localhost:8000/healthz/`.

**Correção:** acrescentar ao `.env` e redeployar.

### Backend `unhealthy` no Traefik, com `400` de `Go-http-client` em `/healthz/`

**Sintoma:** o container está `healthy`, mas o site retorna 502. Nos logs do app,
requisições `400` com `User-Agent: Go-http-client` e `Host: 10.0.x.x`.

**Causa:** o healthcheck do load balancer do Traefik envia o **IP interno da task** no
header `Host`, e esse IP não está em `ALLOWED_HOSTS`.

**Correção:** o label `loadbalancer.healthcheck.hostname=${DOMAIN}`. Se ele saiu vazio, a
causa é `DOMAIN` não exportada no shell no momento do deploy (6.1):

```bash
docker service inspect noivaecia_app --format '{{json .Spec.Labels}}'
```

### Loop infinito de redirect HTTPS

**Sintoma:** `ERR_TOO_MANY_REDIRECTS`.

**Causas, em ordem de frequência:**

1. Cloudflare em **SSL/TLS Flexible** → mudar para **Full (strict)**.
2. `DJANGO_TRUST_X_FORWARDED_PROTO=False` ou ausente → o Django não vê o
   `X-Forwarded-Proto: https` e `SECURE_SSL_REDIRECT` redireciona eternamente.

### Certificado nunca é emitido

```bash
docker service logs noivaecia_traefik 2>&1 | grep -i acme
```

- `unable to find zone` / `Invalid request headers` → token errado, escopo insuficiente
  (falta **Zone → Zone → Read**), ou com `\n` no fim (use `printf '%s'`, nunca `echo`).
- Nenhuma linha de ACME → `tlschallenge` e `dnschallenge` no **mesmo** resolver. Só um
  dos dois pode existir.
- `too many certificates already issued` → limite semanal do Let's Encrypt atingido em
  tentativas repetidas. Use o staging
  (`--certificatesresolvers.letsencrypt.acme.caserver=https://acme-staging-v02.api.letsencrypt.org/directory`)
  enquanto depura, e **apague o `acme.json`** ao voltar para produção.

### Dashboard do Traefik retorna 401 com a senha correta

**Causa:** o `$` do hash foi duplicado no `.env`. A duplicação só vale para hash escrito
literalmente no YAML.

**Correção:** regravar `TRAEFIK_DASHBOARD_AUTH` com a saída crua de `htpasswd -nbB`.

### `failed to resolve host 'db'` ou tabela inexistente na subida

**Sintoma:** erro nos primeiros segundos após o deploy; some sozinho.

**Causa:** o Swarm **ignora `depends_on`**; o `app` pode iniciar antes do `db` aceitar
conexões.

**Correção:** já prevista — healthchecks + `wait_for_db --timeout 90` no entrypoint. Se
o erro **persistir** por mais de um minuto, o problema é outro: confira se o serviço está
na rede `noivascia_internal` e se o host no `DATABASE_URL` bate com o nome do serviço.

### Duas réplicas tentando migrar ao mesmo tempo

Não deve acontecer — o `entrypoint.sh` usa `pg_try_advisory_lock(1)`. Nos logs da réplica
que não pegou o lock, o esperado é `>>> Outra réplica está migrando — aguardando o
lock...` seguido de `>>> Migrations concluídas pela outra réplica.`

Se aparecer erro de migração concorrente, verifique se o `scheduler` não está usando o
`entrypoint.sh` do app por engano — ele deve usar `worker-entrypoint.sh`, que não migra.

### `ACCESS_REFUSED` ou instância desconectada no Evolution API

**Causa:** `EVOLUTION_API_KEY` divergente entre app e serviço, ou o Evolution foi
implantado/recriado sem a instância persistida. Uma instalação nova pode estar saudável e
mesmo assim responder 404: `The "noivascia" instance does not exist`.

**Correção:** conferir que ambos leem a mesma chave e que `EVOLUTION_INSTANCE=noivascia`.
Liste as instâncias pela API usando a chave somente dentro da rede interna. Se a lista
estiver vazia, crie **uma única vez** a instância `noivascia` com `POST /instance/create`,
corpo `{"instanceName":"noivascia","integration":"WHATSAPP-BAILEYS","qrcode":true}`
e a chave no header `apikey`; não exiba a chave nem o QR nos logs. Depois, no sistema,
acesse `/avisos-whatsapp/?connect=1` com a permissão `notifications.manage`, gere o QR e
leia-o no WhatsApp da loja em **Aparelhos conectados**. Por fim, confirme que
`GET /instance/connectionState/noivascia` retorna `open` e rode:

```bash
docker exec -it $APP python manage.py send_daily_whatsapp_report --dry-run --check
```

### Deploy não atualiza o container

**Causa:** a tag da imagem não mudou, então o Swarm considera o serviço convergido.

**Correção:** `docker service update --force <serviço>` — é o que o `deploy.sh` já faz no
passo final. Ou use tags imutáveis por `sha`.

---

## 9. Backup, restore e rotação de segredos

### 9.1 Backup

```bash
cd ~/noivaecia
BACKUP_DIR="$HOME/backups" ./scripts/backup.sh
ls -lh "$HOME/backups"
```

Gera `db_<data>.sql.gz` e `media_<data>.tar.gz`, e remove artefatos com mais de 30 dias.

Agendar diariamente:

```bash
crontab -e
# 0 3 * * * BACKUP_DIR=/home/deploy/backups /home/deploy/noivaecia/scripts/backup.sh >> /home/deploy/backups/cron.log 2>&1
```

> O `pg_dump` roda **dentro do container do Postgres**, não na imagem do app — a imagem
> do Django é `python:3.12-slim` sem `postgresql-client`, de propósito. Por isso o
> management command `golive_backup` não é o caminho de produção em Postgres.

### 9.2 Restore do banco

```bash
DBID=$(docker ps -qf name=noivaecia_db | head -n1)

gunzip -c "$HOME/backups/db_<data>.sql.gz" | docker exec -i "$DBID" psql -U noivas -d noivas_cia
```

Para um dump em formato custom (`pg_dump -Fc`, usado na migração do EasyPanel):

```bash
docker cp "$HOME/backups/<arquivo>.dump" $DBID:/tmp/restore.dump
docker exec -it $DBID pg_restore -U noivas -d noivas_cia --clean --if-exists /tmp/restore.dump
docker exec -it $DBID rm /tmp/restore.dump
```

Depois de qualquer restore, **verifique as sequences** — `--clean` pode deixá-las
dessincronizadas e a próxima locação colidiria com um `id` existente:

```bash
docker exec -it $DBID psql -U noivas -d noivas_cia -c "SELECT last_value FROM rentals_rental_id_seq;"
```

### 9.3 Restore da mídia

```bash
docker run --rm -v noivaecia_media_data:/dest -v "$HOME/backups:/src" alpine \
  sh -c 'cd /dest && tar xzf /src/media_<data>.tar.gz'
```

Confira a contagem de arquivos — a origem de verdade é o número de `proof_photo` não
vazios no banco.

### 9.4 Rotação de segredos

O único Docker Secret é o token do Cloudflare. **Docker Secrets são imutáveis** — não
existe "atualizar no lugar":

```bash
printf '%s' '<NOVO_TOKEN>' | docker secret create CLOUDFLARE_DNS_API_TOKEN_v2 -
# apontar docker-stack.yml para o _v2 e redeployar
docker stack deploy -c docker-stack.yml --with-registry-auth noivaecia
# confirmar que o Traefik subiu, e só então:
docker secret rm CLOUDFLARE_DNS_API_TOKEN
```

Os demais segredos vivem no `.env`: editar o arquivo e redeployar basta.

> Rotacionar `DJANGO_SECRET_KEY` **invalida todas as sessões ativas** — todos os usuários
> precisam logar de novo. Faça em horário de baixo movimento.

### 9.5 Backup para o Google Drive

O `scripts/backup.sh` envia os dois arquivos gerados (`db_*.sql.gz`, `media_*.tar.gz`) para
o Google Drive via `rclone`, se a variável `RCLONE_REMOTE` estiver definida. Sem ela, o
script segue funcionando só com backup local — nada quebra.

Usa **service account**, não OAuth de usuário — não expira, não pede reautenticação em
cron sem sessão interativa.

**1. Criar a service account (uma vez, no Google Cloud Console):**

1. [console.cloud.google.com](https://console.cloud.google.com) → criar projeto (ou usar um
   existente) → **APIs & Services → Library** → ativar **Google Drive API**.
2. **APIs & Services → Credentials → Create Credentials → Service Account** → nome
   `noivascia-backup` → sem papéis no projeto (não precisa).
3. Na service account criada → **Keys → Add Key → Create new key → JSON** → baixa um
   arquivo `.json`. É o único jeito de pegar essa chave — guarde com cuidado.
4. Copie o e-mail da service account (formato
   `noivascia-backup@<projeto>.iam.gserviceaccount.com`).

**2. Compartilhar uma pasta do Drive com ela:**

Crie uma pasta no Google Drive (ex.: "Backups Noivas & Cia") e compartilhe com o e-mail da
service account, papel **Editor**. Service accounts não têm cota própria de storage —
por isso a pasta tem que ser de uma conta real (a sua) que a service account só acessa.

Pegue o ID da pasta pela URL: `drive.google.com/drive/folders/<ID>`.

**3. Instalar e configurar `rclone` na VPS:**

```bash
curl https://rclone.org/install.sh | sudo bash

sudo mkdir -p /home/deploy/.config/rclone
sudo tee /home/deploy/.config/rclone/rclone.conf <<'EOF'
[gdrive]
type = drive
scope = drive
service_account_file = /home/deploy/.secrets/gdrive-backup-sa.json
root_folder_id = <ID_DA_PASTA>
EOF

sudo mkdir -p /home/deploy/.secrets
# copie o .json baixado no passo 1 para /home/deploy/.secrets/gdrive-backup-sa.json
sudo chown -R deploy:deploy /home/deploy/.config/rclone /home/deploy/.secrets
sudo chmod 600 /home/deploy/.secrets/gdrive-backup-sa.json

rclone lsd gdrive:   # deve listar a pasta compartilhada, sem erro
```

**4. Ligar no backup e no cron:**

```bash
crontab -e
# 0 3 * * * BACKUP_DIR=/home/deploy/backups RCLONE_REMOTE=gdrive: /home/deploy/noivaecia/scripts/backup.sh >> /home/deploy/backups/cron.log 2>&1
```

Retenção no Drive é separada da local: `RCLONE_RETENTION_DAYS` (padrão 90 dias) apaga lá,
independente dos 30 dias locais. Ajuste exportando a variável na mesma linha do cron se
quiser outro valor.

**Teste manual antes de confiar no cron:**

```bash
cd ~/noivaecia
BACKUP_DIR=/home/deploy/backups RCLONE_REMOTE=gdrive: ./scripts/backup.sh
rclone ls gdrive:   # os dois arquivos novos devem aparecer
```

---

## 10. Cutover para o domínio de produção

> **Bloqueado até a cliente escolher o domínio.** Todas as demais seções rodam sem essa
> decisão, usando `novo.tonicoimbra.com`.

1. Validar tudo em `novo.tonicoimbra.com` (6.4).
2. Registrar a zona definitiva no Cloudflare; emitir token **Zone → DNS → Edit** +
   **Zone → Zone → Read** para ela e criar o secret `_v2` (9.4).
3. Criar os registros A do domínio definitivo (`@` e `traefik`) → `169.58.79.15`,
   TTL 300, **DNS only** por enquanto.
4. Atualizar as três linhas de domínio no `.env`:

```
DOMAIN=<dominio-definitivo>
DJANGO_ALLOWED_HOSTS=<dominio-definitivo>,.<dominio-definitivo>,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://<dominio-definitivo>,https://*.<dominio-definitivo>
```

5. `./scripts/deploy.sh --skip-build` e verificar a emissão do wildcard da zona nova
   (6.3). **Faça isso antes de mexer no tráfego** — se o certificado não sair, nada foi
   perdido.
6. Rodar backup no ambiente **antigo** e guardar o dump fora da VPS.
7. Parar o ambiente antigo — **nunca deixe os dois aceitando escrita ao mesmo tempo**;
   locações criadas no antigo depois do dump se perdem.
8. Dump final e restore na VPS nova (9.2–9.3).
9. Ligar o proxy do Cloudflare com SSL/TLS em **Full (strict)** e validar HTTPS 200.
10. Observar por 48h: sem 5xx nos logs, relatório diário do WhatsApp entregue **uma única
    vez**.
11. Remover a stack antiga do EasyPanel, preservando o backup pré-migração.
12. Remover o registro `novo` e o secret antigo do Cloudflare.

> **Não ligue `SECURE_HSTS_PRELOAD` antes disso.** O HSTS já está ativo com
> `max-age=31536000`; preload é praticamente irreversível e trava o domínio em HTTPS na
> lista embutida dos navegadores por um ano.

---

## 13. Cutover do sistema legado (dados)

Esta seção cobre a migração de **infra**. A migração dos **dados** do BRcom (VB6/Access),
quando a loja parar de usar o sistema antigo em definitivo, está em
[`runbook-cutover-legado.md`](runbook-cutover-legado.md) — inclui a decisão de fazer
re-import completo em vez de delta incremental, a sequência do cutover e as armadilhas
conhecidas.

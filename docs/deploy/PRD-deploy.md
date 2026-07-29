# PRD — Deploy de produção em VPS (Docker Swarm + Traefik)

> Documento de Requisitos de Deploy · v2.0 · 2026-07-26
> Projeto: Noivas & Cia · Django 5.2.15 · Postgres 16
> Alvo: VPS Contabo `169.58.79.15` · Ubuntu 24.04 · 4 vCPU / 8 GB · Swarm single-node
> **Referência técnica: `C:\PROJETOS\scsi_v1`** — a arquitetura abaixo replica a do
> SCSI, adaptando apenas o que este projeto realmente usa.

> **Este documento não substitui o [`PRD.md`](../../PRD.md) da raiz**, que descreve os
> requisitos de produto. Aqui só se trata de infraestrutura e deploy.

---

## 1. Visão geral e objetivo

O sistema roda hoje em VPS gerenciada por **EasyPanel** (projeto `work`, serviço
`noivaecia`, Postgres 16 em `work/pg`, Evolution API em `work/evolution-api`). A
configuração de infraestrutura existe apenas dentro do painel: não está versionada, não
é reproduzível e não é auditável por diff.

O objetivo é migrar para a mesma arquitetura de deploy do **SCSI** — Docker Swarm +
Traefik, tudo declarado em código no repositório:

1. **Versionada** — o deploy inteiro em arquivos commitados; `git log` é o histórico de
   infra.
2. **Reproduzível** — subir uma VPS idêntica do zero é rodar um script.
3. **Padronizada** — mesma estrutura de arquivos, mesmos nomes de script, mesmas
   convenções do SCSI, para que a operação dos dois sistemas seja idêntica.

> **EasyPanel não é instalado na VPS nova.** Aparece aqui apenas como **ambiente de
> origem** — segue no ar na VPS antiga até o cutover, e dele saem o dump do Postgres, o
> tarball de mídia e a configuração do Evolution API. A VPS `169.58.79.15` roda Docker
> Swarm puro.

### Objetivos não incluídos

- Alta disponibilidade multi-node (o Swarm fica preparado, mas o node é único).
- Stack de observabilidade (`monitoring-stack.yml` do SCSI: Prometheus, Grafana, Loki,
  Promtail, exporters) — fase 2, ver 3.7.
- Qualquer painel de gerenciamento (EasyPanel, Portainer, Coolify).

---

## 2. Diagnóstico do estado atual

### 2.1 Stack e versões

| Item | Noivas & Cia | SCSI (referência) |
|---|---|---|
| Python | 3.12 (`python:3.12-slim`) | 3.13 (`python:3.13-slim`) |
| Django | 5.2.15 | — |
| WSGI | gunicorn 23.0.0, workers sync | gunicorn, `gthread` + 2 threads |
| Estáticos | whitenoise 6.11.0, `CompressedManifestStaticFilesStorage` | whitenoise |
| Driver DB | `psycopg[binary]` — **sem `apt-get`** no Dockerfile | `psycopg2` + `build-essential libpq-dev` |
| CSS | stage `node:20-alpine` + Tailwind 3.4.19 | não aplicável |
| Broker / worker | **nenhum** | RabbitMQ + Celery worker/beat + Redis |

Referências: `Dockerfile:17`, `requirements.txt:2-9`, `docker-entrypoint.sh:15`,
`settings.py:199-206`, `package.json:19`.

### 2.2 Como a configuração é lida

`os.environ` puro, sem `django-environ`, com três helpers em
`noivas_cia/settings.py:21-37`: `_env_bool`, `_env_int`, `_env_list` (split por vírgula).
Settings único; produção derivada de `DJANGO_DEBUG` / `DJANGO_ENV` (`settings.py:40-41`).

Quatro guardas de boot que impedem subir mal configurado — o deploy precisa satisfazê-las:

| Guarda | Linha |
|---|---|
| `DJANGO_SECRET_KEY` ausente com `DEBUG=False` | `settings.py:48-50` |
| Secret com prefixo `changeme` / `django-insecure` | `settings.py:51-57` |
| `DJANGO_ALLOWED_HOSTS` vazio com `DEBUG=False` | `settings.py:62-65` |
| `DATABASE_URL` ausente com `DEBUG=False` | `settings.py:151-153` |

> **Diferença de nomenclatura vs SCSI.** O SCSI usa `DEBUG`, `ALLOWED_HOSTS`,
> `SECRET_KEY`. Este projeto usa `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`,
> `DJANGO_SECRET_KEY`. **Mantemos os nomes deste projeto** — renomear exigiria mexer no
> `settings.py` sem ganho. Os scripts copiados do SCSI são ajustados para os nomes daqui.

### 2.3 Banco de dados

`settings.py:131-153`: com `DATABASE_URL` presente →
`dj_database_url.parse(url, conn_max_age=60, conn_health_checks=True)`. Senão, com
`DEBUG=True`, SQLite local. Senão, `ImproperlyConfigured`.

As credenciais chegam embutidas na `DATABASE_URL`. O SCSI usa variáveis separadas
(`POSTGRES_*`) lidas pelo Django; aqui só o container do Postgres as consome.

### 2.4 Celery, broker, cache, e-mail, mídia

| Componente | Situação |
|---|---|
| Celery | **Inexistente.** Nenhum import, nenhuma task, nenhuma dependência. |
| RabbitMQ | **Inexistente.** Nenhum produtor ou consumidor. |
| Redis | **Inexistente.** Sem `CACHES`; sessões no backend padrão (banco). |
| Trabalho periódico | `scripts/report_scheduler.sh` — loop `sh` de 16 linhas chamando `send_daily_whatsapp_report --if-due` a cada 30s, subido em background pelo entrypoint (`docker-entrypoint.sh:10-13`). Idempotente via `AuditLog`, backoff de 5 min. |
| E-mail | SMTP por env; console backend se `EMAIL_HOST` vazio (`settings.py:210-225`). |
| Estáticos | whitenoise a partir de `STATIC_ROOT` (`settings.py:190`, `:199-206`). |
| Mídia | **Um único** `FileField`: `rentals/models.py:95` (`proof_photo`), `FileSystemStorage`, servido por view autenticada `RentalItemProofPhotoView` (`rentals/views.py:136-163`). |

### 2.5 O que já existe de deploy

`Dockerfile` multi-stage (com `test -s static/css/output.css` em `Dockerfile:14`),
`docker-entrypoint.sh`, `docker-compose.yml`, `.env.example` (33 chaves),
`.dockerignore`. Não existe: stack file, Traefik, CI/CD, `deploy.sh`, `backup.sh`,
`setup_deploy.sh`, Docker Secrets, `wait_for_db`.

### 2.6 Já pronto para a arquitetura-alvo — reutilizar, não recriar

| Exigido | Situação | Referência |
|---|---|---|
| Endpoint de saúde leve, sem DB, sem auth | **Existe: `/healthz/`** | `core/views.py:23-25`, `core/urls.py:6` |
| Rota de saúde isenta de redirect HTTPS | Pronto | `settings.py:231` |
| `SECURE_PROXY_SSL_HEADER` atrás de proxy | Pronto, condicional | `settings.py:240-241` |
| `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` como lista por vírgula | Pronto | `settings.py:59`, `:227` |
| HSTS, cookies seguros, nosniff, XFO | Pronto | `settings.py:228-238` |
| Healthcheck do Postgres (`pg_isready`) | Pronto | `docker-compose.yml:11-15` |

> **Decisão:** o endpoint permanece `/healthz/`. O SCSI usa `/health/`; criar um
> `/health/` paralelo aqui duplicaria rota e quebraria o `SECURE_REDIRECT_EXEMPT`
> existente. **Onde o SCSI escreve `/health/`, este projeto escreve `/healthz/`.**

### 2.7 Gap analysis

| # | Gap | Impacto | Evidência |
|---|---|---|---|
| 1 | `docker-compose.yml` usa `build:` | `docker stack deploy` **ignora** `build:`; o serviço nunca sobe no Swarm | `docker-compose.yml:18` |
| 2 | Sem `docker-stack.yml`, Traefik, redes overlay ou secret | Não há caminho de produção declarado | — |
| 3 | Sem `wait_for_db`; `migrate` sem advisory lock | Com réplicas > 1, duas tasks migram em paralelo | `docker-entrypoint.sh:5` |
| 4 | Scheduler do WhatsApp roda dentro do container do app | Escalar o app para 2 réplicas envia o relatório diário **duas vezes** | `docker-entrypoint.sh:10-13` |
| 5 | Gunicorn com config de desenvolvimento | Sem `gthread`, sem `--max-requests`, sem `--graceful-timeout`; `--forwarded-allow-ips 127.0.0.1` deixa `REMOTE_ADDR` como IP da overlay nos logs | `docker-entrypoint.sh:15-23` |
| 6 | Imagem roda como root, sem `USER` | Escalada de privilégio em caso de RCE. *(O SCSI tem o mesmo gap — é desvio consciente da referência.)* | `Dockerfile` |
| 7 | `pg_dump` ausente na imagem | `golive_backup` **falha** em Postgres | `golive_backup.py:44-63`, `Dockerfile:16` |
| 8 | `.dockerignore` não filtra artefatos grandes | ~630 MB enviados ao daemon a cada build (medido) | `.dockerignore:1-53` |
| 9 | Sem registry, CI, `deploy.sh`, `backup.sh` ou `setup_deploy.sh` | Deploy manual e não repetível | — |
| 10 | Sem bloco `LOGGING` | Erros 500 só no stderr do gunicorn | `settings.py` |
| 11 | Entrypoint não usa o padrão `ENTRYPOINT` + `command` | Impede reaproveitar a mesma imagem para app e scheduler com comandos diferentes | `Dockerfile:38` |

> **Correção em relação à v1 deste documento.** A v1 afirmava que
> `--forwarded-allow-ips 127.0.0.1` causaria loop de redirect HTTPS. Está errado: esse
> flag governa o `wsgi.url_scheme` do gunicorn, enquanto o Django lê
> `HTTP_X_FORWARDED_PROTO` direto do `request.META` via `SECURE_PROXY_SSL_HEADER`
> (`settings.py:240-241`). Por isso o SCSI funciona sem declarar o flag. O impacto real
> é apenas `REMOTE_ADDR` incorreto nos access logs.

Detalhe do gap #8 (medição real na raiz do repositório):

| Caminho | Tamanho | Em `.gitignore`? | Em `.dockerignore`? |
|---|---|---|---|
| `pit/` | 283,6 MB | não | **não** |
| `noivas.mp4` | 110,6 MB | não | **não** |
| `brcom/` | 96,3 MB | sim | **não** |
| `Ana 18 07 2026/` | 96,3 MB | não | **não** |
| `db.sqlite3.zip` | 22,0 MB | sim | **não** |
| `video_noivas_cia_final.mp4` | 15,9 MB | não | **não** |
| `graphify-out/` | 8,6 MB | não | **não** |

As duas listas são independentes — estar em uma não implica estar na outra.

---

## 3. Decisões de arquitetura

Cada componente do template SCSI foi avaliado contra o código real deste projeto.

### 3.1 Celery worker e Celery beat — **EXCLUÍDOS**

Não há uma única task Celery. O único trabalho periódico é o relatório diário do
WhatsApp, hoje um loop `sh` de 16 linhas sobre um comando **já idempotente** (guarda por
`AuditLog`, backoff de 5 min em falha).

Replicar `celery_worker` + `celery_beat` + broker + result backend seria quatro
processos e ~1,2 GB de RAM (somando os limites do SCSI) para substituir um loop que
funciona. **Não se justifica.** Reavaliar se surgirem tasks assíncronas reais.

### 3.2 RabbitMQ — **EXCLUÍDO**

Consequência de 3.1. Sem produtor nem consumidor, seria um serviço sem tráfego.

### 3.3 Redis — **CONDICIONAL, e não para o Django**

O Django não tem `CACHES` e usa sessões em banco. No SCSI, o Redis serve de result
backend do Celery e cache — nada disso existe aqui.

Redis entra **apenas se** o Evolution API exigir (`CACHE_REDIS_URI` é usual em
instalações v2). **Ação obrigatória na S0:** inspecionar as env vars do serviço
`work/evolution-api` no EasyPanel atual e replicá-las literalmente.

### 3.4 Serviço `scheduler` — **INCLUÍDO**, ocupando o lugar do `celery_beat`

Resolve o gap #4. Mesma imagem do `app`, `replicas: 1`, `entrypoint:
["./worker-entrypoint.sh"]` e `command: sh scripts/report_scheduler.sh` — exatamente o
padrão que o SCSI usa para `celery_worker` / `celery_beat`
(`docker-stack.yml:251-252`, `:281-282`).

Ganho: o `app` deixa de ter estado temporal e pode escalar sem duplicar o relatório.

### 3.5 Estáticos com volume — **SEGUE O SCSI**

O SCSI mantém `static_data:/app/staticfiles` e roda `collectstatic --noinput --clear` no
entrypoint do app (`entrypoint.sh:44`). Adotamos o mesmo.

> **Um desvio recomendado:** remover o `--clear`. Com `replicas: 2` no mesmo node, as
> duas tasks montam o mesmo volume; se a réplica B rodar `--clear` enquanto a réplica A
> serve requisições, há uma janela de segundos com o diretório estático vazio. Sem
> `--clear` o efeito é apenas acúmulo de arquivos órfãos entre deploys — trocar um
> risco de 502 intermitente por alguns MB de lixo é o negócio certo.

O `collectstatic` de build time (`Dockerfile:30`) fica como **validação de build** — o
volume o mascara em runtime, mas ele falha cedo se algum static referenciado sumir.

### 3.6 Volume de mídia — **OBRIGATÓRIO**

`proof_photo` grava em disco. Sem volume, todo upload some no próximo deploy. Idêntico
ao `media_data` do SCSI, montado também no `scheduler` (o SCSI monta no
`celery_worker` — `docker-stack.yml:255`).

### 3.7 Observabilidade — **FASE 2**

O SCSI tem um `monitoring-stack.yml` completo (Prometheus, Grafana, Loki, Promtail,
node/postgres/redis exporters) com `scripts/setup_monitoring.sh` e
`scripts/deploy_monitoring.sh`. Fica registrado como fase 2 — replicável quase sem
adaptação depois que a stack principal estabilizar.

Para a primeira virada bastam `/healthz/`, `docker service ps` e os logs do Docker com
rotação (`daemon.json`: `max-size: 20m`, `max-file: 5`).

**Já incluído desde o dia 1**, porque vem de graça no Traefik do SCSI:
`--metrics.prometheus=true` com labels de entrypoints e services
(`docker-stack.yml:43-45`) — as métricas ficam expostas esperando o Prometheus da fase 2.

### 3.8 Serviços do stack

| Serviço | Imagem | Réplicas | Redes | Equivalente no SCSI |
|---|---|---|---|---|
| `traefik` | `traefik:v3.6` | 1 (manager) | `traefik_public` | idêntico |
| `app` | `ghcr.io/elvertoni/noivaecia:latest` | 2 | `traefik_public`, `internal` | `app` |
| `scheduler` | mesma imagem do `app` | **1 (fixo)** | `internal`, `egress` | `celery_beat` |
| `db` | `postgres:16` | 1 | `internal` | `db` |
| `evolution-api` | tag a fixar na S0 | 1 | `internal`, `egress` | — (específico deste projeto) |
| `evolution-redis` | `redis:7` | 1 | `internal` | `redis` *(só se S0 confirmar)* |

**Diferença estrutural em relação à v1 deste documento:** o Traefik fica **dentro do
mesmo `docker-stack.yml`**, como no SCSI, não em stack separada. Um arquivo, um
`docker stack deploy`.

---

## 4. Especificação técnica

### 4.1 Redes overlay — três, como no SCSI

| Rede | Tipo | Quem participa |
|---|---|---|
| `traefik_public` | overlay, `attachable`, **`external: true`** | `traefik`, `app` |
| `noivascia_internal` | overlay, **`internal: true`** | `app`, `scheduler`, `db`, `evolution-api`, `evolution-redis` |
| `noivascia_egress` | overlay (com saída para a internet) | `scheduler`, `evolution-api` |

`internal: true` bloqueia saída para a internet. A rede `egress` existe porque dois
serviços **precisam** de saída: o `evolution-api` fala com os servidores do WhatsApp, e
o `scheduler` pode precisar alcançar SMTP. O `db` fica só na `internal`, sem rota para
fora — é o ponto do desenho.

`traefik_public` é criada **fora** do stack e declarada `external: true`, para sobreviver
a `docker stack rm` e poder ser compartilhada.

### 4.2 Volumes nomeados

| Volume | Montagem | Conteúdo |
|---|---|---|
| `letsencrypt` | `/letsencrypt` (traefik) | `acme.json` — certificados |
| `pg_data` | `/var/lib/postgresql/data` | banco |
| `media_data` | `/app/media` (app + scheduler) | `proof_photo` |
| `static_data` | `/app/staticfiles` (app) | resultado do `collectstatic` |
| `evolution_instances` | conforme S0 | sessão pareada do WhatsApp |

Backups vão para um bind no host (`/backups`, configurável por `BACKUP_DIR`), como no
`scripts/backup.sh` do SCSI.

### 4.3 Segredos — **um Docker Secret, o resto no `.env`**

O SCSI usa exatamente **um** secret externo: `CLOUDFLARE_DNS_API_TOKEN`
(`docker-stack.yml:319-321`). Todo o resto — senha do banco, `SECRET_KEY`, hash do
dashboard — vive no `.env` da VPS com `chmod 600`, entregue aos serviços por `env_file`.

**Adotamos o mesmo modelo**, por dois motivos: é o que o Traefik exige (a convenção
`CF_DNS_API_TOKEN_FILE` é nativa do lego) e é o que mantém a paridade com o SCSI.

```yaml
environment:
  CF_DNS_API_TOKEN_FILE: /run/secrets/CLOUDFLARE_DNS_API_TOKEN
secrets:
  - CLOUDFLARE_DNS_API_TOKEN
```

> **Hardening opcional, fase 2.** O Django não tem convenção `*_FILE` nativa. Um helper
> `_env_secret(name)` em `settings.py` — que lê `${NAME}_FILE` antes de `${NAME}`,
> seguindo o padrão de `_env_bool`/`_env_int`/`_env_list` (`settings.py:21-37`) —
> permitiria mover `DJANGO_SECRET_KEY` e `DATABASE_URL` para Docker Secrets. Fica
> **fora** da paridade com o SCSI; implementar só depois da virada estabilizar.

### 4.4 Domínio — variável única `DOMAIN`

O domínio definitivo ainda não foi escolhido pela cliente. Seguindo o SCSI, **uma única
variável** governa tudo (`.env.example:52-59` do SCSI):

| Variável | Uso |
|---|---|
| `DOMAIN` | `Host(\`${DOMAIN}\`)` do app, `Host(\`traefik.${DOMAIN}\`)` do dashboard, e o certificado wildcard `${DOMAIN}` + `*.${DOMAIN}` |
| `ACME_EMAIL` | conta Let's Encrypt |
| `TRAEFIK_DASHBOARD_AUTH` | hash do Basic Auth do dashboard |

Nenhum arquivo versionado contém o domínio literal. O `docker stack deploy` interpola
`${DOMAIN}` a partir do ambiente do shell — por isso `deploy.sh` carrega o `.env` para o
ambiente antes de chamar o deploy (`scripts/deploy.sh:49-61` do SCSI).

**Enquanto a cliente decide**, use `DOMAIN=novo.tonicoimbra.com` — zona já sob controle
no Cloudflare. Isso permite executar e validar S1–S7 por inteiro, incluindo emissão de
wildcard por DNS-01. Trocar depois é editar três linhas do `.env` e redeployar; nenhuma
mudança de código.

> Se a zona final for diferente de `tonicoimbra.com`, o token do Cloudflare precisa
> cobrir a zona nova — tokens ACME são por zona.

**Padrão dos hosts** (espelha `.env.example:11-12` do SCSI, com os nomes de variável
deste projeto):

```
DJANGO_ALLOWED_HOSTS=${DOMAIN},.${DOMAIN},localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://*.${DOMAIN}
```

- `.${DOMAIN}` (ponto inicial) cobre subdomínios.
- **`localhost` e `127.0.0.1` são obrigatórios** — o `HEALTHCHECK` do container bate em
  `http://localhost:8000/healthz/`. Sem eles, 400 `DisallowedHost` e a task nunca fica
  `healthy`.
- Em `ALLOWED_HOSTS` só o hostname; em `CSRF_TRUSTED_ORIGINS` sempre com esquema.

### 4.5 Traefik e TLS — cópia do SCSI

`traefik:v3.6`, provider **`--providers.swarm`** (não o `docker` legado), `exposedByDefault=false`.

```
--providers.swarm=true
--providers.swarm.endpoint=unix:///var/run/docker.sock
--providers.swarm.exposedByDefault=false
--providers.swarm.network=traefik_public

--entrypoints.web.address=:80
--entrypoints.web.http.redirections.entrypoint.to=websecure
--entrypoints.web.http.redirections.entrypoint.scheme=https
--entrypoints.websecure.address=:443
--entrypoints.websecure.forwardedHeaders.trustedIPs=<faixas Cloudflare>

--certificatesresolvers.letsencrypt.acme.email=${ACME_EMAIL}
--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json
--certificatesresolvers.letsencrypt.acme.dnschallenge=true
--certificatesresolvers.letsencrypt.acme.dnschallenge.provider=cloudflare
--certificatesresolvers.letsencrypt.acme.dnschallenge.delaybeforecheck=10
--certificatesresolvers.letsencrypt.acme.dnschallenge.resolvers=1.1.1.1:53,8.8.8.8:53

--entrypoints.websecure.http.tls.certresolver=letsencrypt
--entrypoints.websecure.http.tls.domains[0].main=${DOMAIN}
--entrypoints.websecure.http.tls.domains[0].sans=*.${DOMAIN}

--metrics.prometheus=true
--metrics.prometheus.addEntryPointsLabels=true
--metrics.prometheus.addServicesLabels=true
```

Pontos que o SCSI já resolveu e que copiamos sem discutir:

- **Wildcard declarado no entrypoint**, não por router (`docker-stack.yml:33-35`). Um
  certificado emitido uma vez cobre todos os subdomínios; routers novos não disparam
  emissão nova.
- **`delaybeforecheck=10`** — dá folga para o TXT `_acme-challenge` propagar antes do
  lego verificar.
- **Nunca combinar `tlschallenge` com `dnschallenge`** no mesmo resolver.
- Faixas de IP do Cloudflare em `forwardedHeaders.trustedIPs`, copiadas de
  `docker-stack.yml:22`. **Registrar a data da cópia** — a lista muda
  (<https://www.cloudflare.com/ips/>).

**Basic Auth do dashboard — detalhe que inverte o senso comum:**

```yaml
- "traefik.http.middlewares.traefik-auth.basicauth.users=${TRAEFIK_DASHBOARD_AUTH}"
```

O hash vai no `.env` **com um único `$`**, exatamente como sai de `htpasswd -nbB admin
'SENHA'`. A regra de duplicar `$$` vale para hash escrito **literalmente** no YAML;
quando ele chega por variável interpolada, duplicar quebra o hash. O SCSI documenta isso
em `docker-stack.yml:82-85`.

**Labels do serviço `app`:**

```yaml
- "traefik.enable=true"
- "traefik.http.routers.noivascia.rule=Host(`${DOMAIN}`)"
- "traefik.http.routers.noivascia.entrypoints=websecure"
- "traefik.http.routers.noivascia.tls=true"
- "traefik.http.routers.noivascia.tls.certresolver=letsencrypt"
- "traefik.http.services.noivascia.loadbalancer.server.port=8000"
- "traefik.http.services.noivascia.loadbalancer.healthcheck.path=/healthz/"
- "traefik.http.services.noivascia.loadbalancer.healthcheck.interval=15s"
- "traefik.http.services.noivascia.loadbalancer.healthcheck.hostname=${DOMAIN}"
- "traefik.http.middlewares.noivascia-ratelimit.ratelimit.average=100"
- "traefik.http.middlewares.noivascia-ratelimit.ratelimit.burst=50"
- "traefik.http.routers.noivascia.middlewares=noivascia-ratelimit"
```

> **`healthcheck.hostname` não é opcional.** Sem ele o Traefik envia o IP interno da
> task (`10.0.x.x`) no header `Host`; esse IP não está em `ALLOWED_HOSTS`, o Django
> responde `400`, e o Traefik marca o backend unhealthy — o site cai com 502 mesmo com
> o container saudável. O healthcheck do *container* não sofre disso porque usa
> `localhost`. O SCSI comenta exatamente isso em `docker-stack.yml:151-152`.

**Cloudflare em modo proxy** exige SSL/TLS em **Full (strict)**. Em `Flexible`, o
Cloudflare fala HTTP com o Traefik, o Django recebe `X-Forwarded-Proto: http`,
`SECURE_SSL_REDIRECT` (`settings.py:230`) redireciona, e o loop se fecha.

### 4.6 Gunicorn — via `command`, com a config do SCSI

O comando sai do entrypoint e vai para o `command:` do stack file
(`docker-stack.yml:99-111`), adaptado a este projeto (`noivas_cia.wsgi`, não `core.wsgi`):

```yaml
command: >
  gunicorn noivas_cia.wsgi:application
  --bind 0.0.0.0:8000
  --workers 4
  --worker-class gthread
  --threads 2
  --timeout 120
  --max-requests 1000
  --max-requests-jitter 50
  --graceful-timeout 30
  --keep-alive 5
  --forwarded-allow-ips *
  --access-logfile -
  --error-logfile -
```

Diferenças em relação ao que roda hoje (`docker-entrypoint.sh:15-23`): `gthread` com 2
threads em vez de workers sync puros; `--max-requests` reciclando workers contra
vazamento de memória; `--graceful-timeout` e `--keep-alive` explícitos.

`--forwarded-allow-ips *` é o único acréscimo ao comando do SCSI. Seguro nesta topologia
— a porta do app nunca é publicada no host, só o Traefik alcança pela overlay — e
corrige o `REMOTE_ADDR` nos access logs.

### 4.7 Entrypoints — padrão `ENTRYPOINT` + `command`

O SCSI usa `ENTRYPOINT ["./entrypoint.sh"]` no Dockerfile e termina o script com
`exec "$@"`, deixando o `command:` do stack decidir o processo. Isso é o que permite a
**mesma imagem** servir app e scheduler. Adotamos (resolve o gap #11).

Renomeamos `docker-entrypoint.sh` → **`entrypoint.sh`** e criamos
**`worker-entrypoint.sh`**, mantendo os nomes do SCSI.

**`entrypoint.sh`** (serviço `app`) — porte direto de `scsi_v1/entrypoint.sh`, trocando
`core.settings` por `noivas_cia.settings`:

```sh
#!/bin/sh
set -e

echo ">>> Aguardando o banco de dados..."
python manage.py wait_for_db --timeout 90

echo ">>> Aplicando migrations (com advisory lock para multi-réplica)..."
python <<'PY'
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'noivas_cia.settings')
django.setup()

from django.core.management import call_command
from django.db import connection

if connection.vendor == 'postgresql':
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(1)')
        acquired = cursor.fetchone()[0]
        if acquired:
            try:
                print('>>> Lock adquirido — executando migrations...')
                call_command('migrate', '--noinput')
            finally:
                cursor.execute('SELECT pg_advisory_unlock(1)')
            print('>>> Migrations concluídas e lock liberado.')
        else:
            print('>>> Outra réplica está migrando — aguardando o lock...')
            cursor.execute('SELECT pg_advisory_lock(1)')
            cursor.execute('SELECT pg_advisory_unlock(1)')
            print('>>> Migrations concluídas pela outra réplica.')
else:
    call_command('migrate', '--noinput')
PY

echo ">>> Coletando arquivos estáticos..."
python manage.py collectstatic --noinput

exec "$@"
```

O `pg_try_advisory_lock(1)` não bloqueia: quem pega o lock migra; quem não pega espera
no `pg_advisory_lock(1)` e segue quando a outra réplica termina. (Único desvio: sem
`--clear` no collectstatic — ver 3.5.)

**`worker-entrypoint.sh`** (serviço `scheduler`) — porte direto de
`scsi_v1/worker-entrypoint.sh`:

```sh
#!/bin/sh
set -e

echo ">>> [scheduler] Aguardando o banco de dados..."
python manage.py wait_for_db --timeout 90

exec "$@"
```

Não migra, não coleta estáticos. O `command:` do serviço passa a ser
`sh scripts/report_scheduler.sh`, e o script atual (`scripts/report_scheduler.sh`)
**não muda**.

**Comando novo** `core/management/commands/wait_for_db.py` — porte direto de
`scsi_v1/base/management/commands/wait_for_db.py`: `--timeout` (default 60) e
`--interval` (default 2.0), loop sobre `connections['default'].cursor()` capturando
`OperationalError`, `SystemExit(1)` ao estourar o deadline.

**`WHATSAPP_SCHEDULER_ENABLED`** deixa de ter função no entrypoint (o scheduler virou
serviço); desligá-lo agora é `docker service scale noivascia_scheduler=0`. Manter a
variável no `.env` para o `docker-compose.yml` local.

### 4.8 Healthchecks

| Serviço | Teste | `start_period` |
|---|---|---|
| `app` | `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz/')"` — forma do SCSI (`docker-stack.yml:120`), com `/healthz/` | 60s |
| `db` | `pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}` | 30s |
| `evolution-api` | conectividade na porta HTTP interna | 60s |
| `evolution-redis` | `redis-cli ping` | 10s |
| `scheduler` | **sem healthcheck** — processo de background sem porta, igual ao `celery_beat` do SCSI | — |

**O Swarm ignora `depends_on`.** A ordem vem de healthcheck + `wait_for_db`.

### 4.9 Resiliência e zero-downtime

`restart_policy` em todos os serviços (`condition: on-failure`, `delay: 10s`,
`max_attempts: 5`, `window: 120s` — valores do SCSI).

`update_config` / `rollback_config` no `app` (`docker-stack.yml:127-136`):

```yaml
update_config:
  parallelism: 1
  delay: 15s
  order: start-first
  failure_action: rollback
  monitor: 30s
rollback_config:
  parallelism: 1
  delay: 5s
  order: stop-first
```

O `db` usa `order: stop-first` — duas instâncias no mesmo volume corrompem os dados.

**Orçamento de recursos** dos 8 GB, partindo dos valores do SCSI e redistribuindo o que
Celery/RabbitMQ/Redis liberaram:

| Serviço | Limite | Reserva | Origem |
|---|---|---|---|
| `traefik` | 0.5 cpu / 192M | 0.15 / 64M | igual ao SCSI |
| `app` (×2) | 1.0 cpu / 768M | 0.25 / 256M | SCSI usa 512M; folga de Celery |
| `scheduler` | 0.25 cpu / 256M | 0.05 / 128M | igual ao `celery_beat` |
| `db` | 1.5 cpu / 2048M | 0.25 / 512M | SCSI usa 1024M; folga de RabbitMQ |
| `evolution-api` | 1.0 cpu / 1024M | 0.25 / 256M | específico daqui |
| `evolution-redis` | 0.3 cpu / 384M | 0.1 / 64M | igual ao `redis` do SCSI |

Total de limites ≈ 5,4 GB, deixando ~2,6 GB para o SO, o buffer cache do Postgres e a
stack de observabilidade da fase 2.

### 4.10 Scripts — os quatro do SCSI

Todos portados de `scsi_v1/scripts/`, ajustados para os nomes deste projeto
(`STACK_NAME=noivascia`, `IMAGE=ghcr.io/elvertoni/noivaecia`, `DJANGO_DEBUG` em vez de
`DEBUG`, `DJANGO_ALLOWED_HOSTS` em vez de `ALLOWED_HOSTS`, serviços `app scheduler` em
vez de `app celery_worker celery_beat`).

**`scripts/deploy.sh`** — ciclo completo na VPS:

1. Pré-condições: `docker` presente, Swarm `active`.
2. Carrega `.env` com **parser seguro** de `KEY=VALUE` (`scripts/deploy.sh:49-61`).
   **Nunca `source`** — valores com `& $ * @` quebram o shell, e o
   `DEFAULT_FROM_EMAIL` deste projeto já contém um `&` (`.env.example:46`). O mesmo
   parser exporta `DOMAIN` para a interpolação do stack file.
3. Valida: secret `CLOUDFLARE_DNS_API_TOKEN` existe, rede `traefik_public` existe,
   `DJANGO_DEBUG=False`, `localhost` em `DJANGO_ALLOWED_HOSTS`.
4. `git pull` → `docker build` (tags `:<sha-curto>` e `:latest`) → `docker push`, com
   login automático no GHCR se `GITHUB_TOKEN` estiver definido.
5. `docker stack deploy -c docker-stack.yml --with-registry-auth noivascia`.
6. `docker service update --force` em `noivascia_app` e `noivascia_scheduler`.
7. Imprime status e comandos úteis.

Flag **`--skip-build`**: pula git pull, build e push — é o caminho quando o GitHub
Actions já publicou a imagem.

**`scripts/backup.sh`** — porte de `scsi_v1/scripts/backup.sh`:

- `pg_dump` **executado dentro do container do `db`** via `docker exec`, saída em
  `gzip`. Isso contorna o gap #7 sem instalar `postgresql-client` na imagem do app (que
  evita `apt-get` de propósito — `Dockerfile:16`).
- Mídia por `docker run --rm -v noivascia_media_data:/data:ro alpine tar czf`.
- Rotação: `find "$BACKUP_DIR" -name "*.gz" -mtime +30 -delete`.

> O comando `core/golive_backup` continua útil pelo **manifesto com sha256 e contagens
> de 11 modelos** (`golive_backup.py:96-164`), mas seu caminho de Postgres depende de
> `pg_dump` local. `backup.sh` é o caminho de produção.

**`scripts/setup_deploy.sh`** — bootstrap idempotente da VPS do zero, em duas fases
(root prepara o sistema e cria o usuário `deploy`; o script então re-executa a si mesmo
como `deploy` e conduz chave SSH, clone, login no GHCR, `.env`, redes, build/push,
Basic Auth, secret do Cloudflare, deploy e verificação).

> **Dois defeitos do original a corrigir no porte.** O `setup_deploy.sh` do SCSI escreve
> uma jail `[ssh]` com `logpath = /var/log/auth.log` (`setup_deploy.sh:282-291`). No
> Ubuntu 24.04 a jail chama-se **`sshd`** e o backend precisa ser **`systemd`** — o
> bloco como está é silenciosamente ignorado e a jail roda com os defaults da
> distribuição. E o script concede `NOPASSWD:ALL` ao usuário `deploy`
> (`setup_deploy.sh:430`); aceitável num tutorial, questionável em produção.

**`.github/workflows/deploy.yml`** — complementa, não substitui, o `deploy.sh`:
build e push para o GHCR no `push` em `main`, `platforms: linux/amd64`, cache de layers,
tags `:<sha>` e `:latest`. Com o CI ativo, o fluxo normal na VPS passa a ser
`./scripts/deploy.sh --skip-build`.

### 4.11 Compose local

`docker-compose.yml` continua com `build:` — é o ambiente de desenvolvimento. Ajustes:
adicionar o serviço `scheduler` com `entrypoint: ["./worker-entrypoint.sh"]` e
`command: sh scripts/report_scheduler.sh`, e mover o comando do gunicorn do entrypoint
para o `command:` do serviço `app`, espelhando o stack file.

---

## 5. Sprints de implementação

Ordem lógica; cada tarefa nomeia o arquivo afetado e o critério de pronto.

### S0 — Preparação e auditoria

- [ ] Auditar env vars de `work/noivaecia` no EasyPanel (registrar **nomes**, não
  valores). **Pronto:** lista anexada.
- [ ] Auditar `work/pg` — versão exata, nome do banco, usuário. **Pronto:** versão
  confirmada e registrada.
- [ ] Auditar `work/evolution-api` — imagem/tag, env vars, volumes, se usa Postgres
  próprio e/ou Redis. **Pronto:** decisão sobre `evolution-redis` (3.3) tomada com base
  em fato.
- [ ] **Segurança do host:** mover o hardening de SSH de `/etc/ssh/ssh_config` (arquivo
  do *cliente*, sem efeito) para `/etc/ssh/sshd_config.d/99-hardening.conf`.
  **Pronto:** `sudo sshd -T | grep -E 'permitrootlogin|passwordauthentication'` retorna
  `no` nos dois, e uma sessão paralela por chave continua funcionando.
- [ ] **Segurança do host:** corrigir `/etc/fail2ban/jail.local` para `[sshd]` com
  `backend = systemd`. **Pronto:** `sudo fail2ban-client get sshd maxretry` retorna `3`.
- [ ] **Rotacionar o bearer token do EasyPanel** exposto em texto puro em
  `var/create_media_mount.mjs:4`. **Pronto:** token antigo revogado no painel.
- [ ] Criar o registro A `novo.tonicoimbra.com` → `169.58.79.15`, **DNS only**, TTL 300.
  **Pronto:** `dig +short novo.tonicoimbra.com` retorna o IP.
- [ ] Emitir token Cloudflare **Zone → DNS → Edit** + **Zone → Zone → Read** na zona
  `tonicoimbra.com` e criar o secret. **Pronto:** `docker secret ls` lista
  `CLOUDFLARE_DNS_API_TOKEN`.

### S1 — Imagem e entrypoints

- [ ] `.dockerignore`: adicionar `pit/`, `graphify-out/`, `prints/`, `Ana*/`, `brcom/`,
  `*.mp4`, `*.pptx`, `*.zip`, `*.pdf`, `legado.jpeg`. **Pronto:** contexto de build
  abaixo de 50 MB (hoje ~630 MB).
- [ ] `Dockerfile`: trocar `CMD ["./docker-entrypoint.sh"]` por
  `ENTRYPOINT ["./entrypoint.sh"]`; `chmod +x entrypoint.sh worker-entrypoint.sh`.
  **Pronto:** `docker run <img> gunicorn --version` executa o entrypoint e depois o
  comando passado.
- [ ] `Dockerfile`: criar usuário não-root e `USER` antes do `ENTRYPOINT`, com
  `/app/media` e `/app/staticfiles` sob sua posse. *(Desvio consciente do SCSI, que roda
  como root.)* **Pronto:** `docker run ... id -u` retorna diferente de `0` e o app sobe.
- [ ] `core/management/commands/wait_for_db.py`: porte de
  `scsi_v1/base/management/commands/wait_for_db.py`. **Pronto:** `python manage.py
  wait_for_db --timeout 5` sai com código 1 e mensagem clara com o banco fora.
- [ ] `entrypoint.sh` (renomeado de `docker-entrypoint.sh`): `wait_for_db` → migrations
  com advisory lock → `collectstatic` → `exec "$@"` (4.7). **Pronto:** subir duas
  réplicas e confirmar nos logs que só uma aplica migrations e a outra reporta
  "Migrations concluídas pela outra réplica".
- [ ] `worker-entrypoint.sh`: arquivo novo (4.7). **Pronto:** o container do scheduler
  loga `[scheduler] WhatsApp daily report scheduler started` e **não** loga `Aplicando
  migrations`.

### S2 — Settings e configuração

- [ ] `noivas_cia/settings.py`: bloco `LOGGING` — console com timestamp e nível,
  `django.request` em `ERROR`, nível raiz por `DJANGO_LOG_LEVEL`. **Pronto:** um 500
  forçado aparece formatado em `docker service logs`.
- [ ] `.env.example`: adicionar `DOMAIN`, `ACME_EMAIL`, `TRAEFIK_DASHBOARD_AUTH`; usar o
  padrão de hosts de 4.4; remover a linha obsoleta `DATABASE_NAME` (`.env.example:18`,
  resquício de SQLite). **Pronto:** copiar o exemplo e preencher os segredos resulta num
  `.env` que sobe.
- [ ] `README.md`: corrigir a tabela de variáveis (linhas 98-114), hoje descrevendo
  SQLite/`DATABASE_NAME` sem mencionar `DATABASE_URL`/Postgres/Evolution. **Pronto:**
  tabela reflete o `settings.py` atual.

### S3 — Endpoint de saúde

- [ ] Confirmar `/healthz/` (`core/views.py:23-25`): 200, sem DB, sem auth, isento de
  redirect (`settings.py:231`). **Pronto:** verificado; nenhum código novo.
- [ ] Documentar que onde o SCSI usa `/health/`, este projeto usa `/healthz/`.
  **Pronto:** o path aparece uma única vez no guia, referenciado dos demais pontos.

### S4 — Compose local

- [ ] `docker-compose.yml`: serviço `scheduler` com `entrypoint:
  ["./worker-entrypoint.sh"]`; comando do gunicorn movido para `command:` do `app`
  (4.11). **Pronto:** `docker compose up --build` sobe `app` e `scheduler` separados e
  serve CSS/JS.

### S5 — Stack Swarm

- [ ] `docker-stack.yml`: novo arquivo com `traefik`, `app`, `scheduler`, `db`,
  `evolution-api` (+ `evolution-redis` se S0 confirmar), usando `image:` do GHCR.
  **Pronto:** `docker stack config -c docker-stack.yml` valida sem erro.
- [ ] Três redes (4.1), cinco volumes (4.2), secret externo (4.3). **Pronto:**
  `docker stack deploy` cria as redes e monta o secret em `/run/secrets/`.
- [ ] Healthchecks (4.8), `restart_policy` e `resources` (4.9) em todos os serviços.
  **Pronto:** `docker service ls` estável por 5 minutos; `docker stats` confirma os
  limites.
- [ ] `update_config` / `rollback_config` no `app` (4.9). **Pronto:** deploy de uma
  imagem propositalmente quebrada dispara rollback automático e o site permanece no ar.

### S6 — Traefik e Cloudflare

- [ ] Serviço `traefik` no `docker-stack.yml` com a configuração de 4.5. **Pronto:**
  dashboard responde em `https://traefik.${DOMAIN}` pedindo Basic Auth.
- [ ] `TRAEFIK_DASHBOARD_AUTH` no `.env` com hash de `htpasswd -nbB`, **um único `$`**
  (4.5). **Pronto:** acesso sem credencial retorna 401; com credencial, 200.
- [ ] `forwardedHeaders.trustedIPs` com as faixas do Cloudflare e comentário registrando
  a data da cópia. **Pronto:** access logs mostram o IP real do cliente.
- [ ] Labels do `app` com `Host(\`${DOMAIN}\`)`, `healthcheck.path=/healthz/`,
  `healthcheck.hostname=${DOMAIN}` e o middleware de rate limit. **Pronto:** nos logs do
  Traefik não aparece nenhum `400` de `Go-http-client` em `/healthz/`.
- [ ] Emissão do wildcard `${DOMAIN}` + `*.${DOMAIN}`. **Pronto:** `openssl s_client
  -connect novo.tonicoimbra.com:443` mostra o SAN wildcard e emissor Let's Encrypt.
- [ ] Verificar que nenhum arquivo versionado tem o domínio literal:
  `grep -rn 'tonicoimbra' docker-stack.yml scripts/` não retorna nada. **Pronto:** trocar
  de domínio é editar só o `.env`.

### S7 — Scripts e CI

- [ ] `scripts/deploy.sh` — porte de `scsi_v1/scripts/deploy.sh` (4.10). **Pronto:**
  rodar com `DJANGO_DEBUG=True` no `.env` emite aviso, e sem o secret do Cloudflare
  aborta antes de qualquer `docker stack deploy`.
- [ ] `scripts/backup.sh` — porte de `scsi_v1/scripts/backup.sh` (4.10). **Pronto:** um
  ciclo gera `db_<data>.sql.gz` + `media_<data>.tar.gz` e remove artefatos além de 30 dias.
- [ ] `scripts/setup_deploy.sh` — porte de `scsi_v1/scripts/setup_deploy.sh`, **com as
  duas correções** de 4.10 (jail `[sshd]`/`backend=systemd`; revisar o `NOPASSWD`).
  **Pronto:** rodar numa VPS limpa leva do zero ao stack no ar; rodar de novo é
  idempotente.
- [ ] `.github/workflows/deploy.yml` (4.10). **Pronto:** push em `main` publica
  `ghcr.io/elvertoni/noivaecia:<sha>` e `:latest`.
- [ ] Agendar `backup.sh` por cron no host, diário. **Pronto:** `crontab -l` mostra a
  entrada e o primeiro backup automático existe.

### S8 — Migração de dados

- [ ] `pg_dump -Fc` do banco em `work/pg`, com o app antigo **parado**. **Pronto:** dump
  com tamanho compatível e `pg_restore --list` legível.
- [ ] Restore na VPS nova. **Pronto:** contagens por tabela batem com a origem.
- [ ] `tar` do volume de mídia do EasyPanel → restore em `noivascia_media_data`.
  **Pronto:** contagem de arquivos em `rentals/proof_photos/` igual na origem e no destino.
- [ ] Subir `evolution-api` com a configuração auditada na S0; parear o QR da instância
  `noivascia`. **Pronto:** `send_daily_whatsapp_report --dry-run --check` reporta a
  instância conectada.
- [ ] Validação pós-restore reaproveitando a lógica de
  `tools/db_transfer/verify_pg_migration.py` (contagens, sequences vs `MAX(id)`,
  checksums financeiros). **Pronto:** relatório sem divergências.
- [ ] Conferir sequences após o restore. **Pronto:** criar uma locação de teste não
  colide com `id` existente.

### S9 — Cutover

> **Depende da escolha do domínio definitivo pela cliente.** As demais sprints não.

- [ ] Registrar a zona definitiva no Cloudflare, emitir token para ela, criar o secret
  `CLOUDFLARE_DNS_API_TOKEN_v2` e apontar o stack file. **Pronto:** zona ativa.
- [ ] Smoke test completo em `novo.tonicoimbra.com`: login por e-mail; dashboard; criar
  locação; gerar o PDF do contrato; upload e leitura de `proof_photo`; registrar
  recebimento com juros; `send_daily_whatsapp_report --dry-run`. **Pronto:** os sete
  passos sem erro.
- [ ] Verificar cabeçalhos de segurança (HSTS, `X-Frame-Options`, `Content-Type-Options`,
  cookies `Secure`). **Pronto:** conferidos na resposta HTTP.
- [ ] Atualizar `DOMAIN` e os hosts no `.env` da VPS e redeployar; confirmar emissão do
  wildcard da zona nova **antes** de mexer no tráfego. **Pronto:** certificado emitido.
- [ ] Criar o A record do domínio definitivo → `169.58.79.15`. **Pronto:** HTTPS 200 no
  domínio de produção.
- [ ] Janela de observação de 48h com o EasyPanel antigo **parado mas preservado**.
  **Pronto:** sem 5xx nos logs e relatório diário entregue uma única vez.
- [ ] Remover a stack antiga do EasyPanel, mantendo o backup pré-migração. **Pronto:**
  recursos liberados e backup arquivado fora da VPS.
- [ ] Atualizar `CLAUDE.md` e `AGENTS.md`, que hoje descrevem deploy por EasyPanel
  (`CLAUDE.md:8-12`, `AGENTS.md:38-43`). **Pronto:** documentos refletem Swarm + GHCR.

---

## 6. Riscos e pontos de atenção

| Risco | Impacto | Mitigação |
|---|---|---|
| **Perda de escritas na janela de migração** | Locações ou recebimentos criados após o dump se perdem | Parar o app antigo antes do `pg_dump`; janela de baixo movimento; nunca deixar os dois ambientes aceitando escrita |
| **Perda de volume** | `docker stack rm` não apaga volumes, mas `docker volume prune` sim | Backup antes de operações de stack; nunca `prune` sem `--filter` |
| **Rotação de segredos** | Docker Secret é **imutável** | Convenção `nome_v2`, atualizar o stack, remover o antigo depois |
| **`$` no hash do Basic Auth** | Duplicar `$$` quebra o hash quando ele vem por variável | Hash cru (um `$`) no `.env`; `$$` só se escrito literalmente no YAML |
| **Arquitetura de build** | Runner do GH Actions e VPS (AMD EPYC) são ambos amd64 | Fixar `platforms: linux/amd64` para não regredir |
| **Cloudflare em Flexible** | Loop infinito de redirect | SSL/TLS em **Full (strict)**; validar antes do cutover |
| **HSTS já ativo** | `SECURE_HSTS_SECONDS=31536000` (`settings.py:236`) trava o domínio em HTTPS por um ano | Não ligar `SECURE_HSTS_PRELOAD` antes da virada estabilizar |
| **Wildcard cobre a zona inteira** | O Traefik passa a deter certificado para `*.${DOMAIN}` | Aceito conscientemente — é o desenho do SCSI |
| **Faixas de IP do Cloudflare mudam** | `trustedIPs` desatualizado mascara o IP real | Registrar a data da cópia; revisar semestralmente |
| **`collectstatic --clear` com 2 réplicas** | Janela de segundos com estáticos vazios | Omitir `--clear` (desvio documentado em 3.5) |
| **`replicas > 1` sem o `scheduler` separado** | Relatório diário duplicado ao cliente | S1 é bloqueante para S5 |
| **`setup_deploy.sh` herda defeitos do original** | Jail do fail2ban inerte; `NOPASSWD:ALL` no usuário deploy | Corrigir no porte (4.10) |
| **Domínio definitivo indefinido** | Hardcodar agora obrigaria a caçar ocorrências depois | Tudo por `${DOMAIN}`; tarefa de S6 verifica por `grep` |

---

## 7. Referências

- **Projeto de referência**: `C:\PROJETOS\scsi_v1` — `docker-stack.yml`, `entrypoint.sh`,
  `worker-entrypoint.sh`, `scripts/{deploy,backup,setup_deploy}.sh`,
  `base/management/commands/wait_for_db.py`, `monitoring-stack.yml` (fase 2)
- Guia operacional: [`docs/deploy/guia-vps.md`](guia-vps.md)
- Arquitetura da aplicação: [`docs/arquitetura.md`](../arquitetura.md)
- Migração SQLite → Postgres (2026-07-20): [`db-migra.md`](../../db-migra.md)
- Notas do WhatsApp / Evolution API: [`whats.md`](../../whats.md)

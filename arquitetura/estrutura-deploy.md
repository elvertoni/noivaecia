# CONTEXTO E PAPEL
Você é um engenheiro de plataforma especialista em Django + Docker Swarm. O
projeto Django **já existe** (o código está neste repositório, `noivas-cia`) e
está **em produção**, atendendo uma loja real. Quero implementar nele uma
arquitetura de deploy de produção em VPS, padronizada e replicável — um "template
de deploy" aplicável a qualquer projeto Django.

**Não assuma nada sobre o projeto sem antes inspecionar o código.** O diagnóstico
da Etapa 1 já foi levantado e está abaixo; confirme cada ponto no código antes de
usá-lo, porque ele envelhece.

Parâmetros do deploy:
- **Domínio:** `noivaseciabandeirantes.com.br`
- **Registry de imagens:** `ghcr.io/elvertoni/noivaecia`
- **Nome do stack no Swarm:** `noivascia`
- **Provedor de DNS para TLS:** Cloudflare (token de API com escopo
  Zone > DNS > Edit **e** Zone > Zone > Read na zona do domínio)
- **Servidor:** VPS Ubuntu, Docker Swarm single-node (deve poder escalar)
- **Origem:** hoje roda no EasyPanel em `noivasecia.tonicoimbra.com`; a virada
  inclui **migrar a base de dados** e aposentar o EasyPanel

> **Escopo decidido com o dono do produto (2026-08-02):**
> - **Dentro:** arquitetura de deploy completa (Swarm, Traefik, DNS-01, secrets,
>   scripts) e migração da base. **Sem mudar versão de Python ou Django.**
> - **Fora:** multi-tenant, agentes de IA, **Celery/RabbitMQ/Redis** e **upgrade
>   de Python/Django**.
>
> **Por que o upgrade ficou fora.** O template pede Python >3.13 e Django >6.0 —
> correto para projeto novo. Este está em produção numa LTS. Calendário oficial
> (consultado 2026-08-02): **5.2 LTS suportado até abril/2028**; o 6.0 só até
> abril/2027, com mainstream encerrando em agosto/2026. Subir agora encurtaria o
> suporte em um ano. O caminho é LTS→LTS: avaliar **6.2 LTS** em abril/2027.
>
> **Por que Celery ficou fora.** O template de origem o exige para "processamento
> do agente de IA principalmente" — não há IA aqui, então o requisito perde a
> razão de ser. A carga assíncrona real é um relatório diário de WhatsApp e
> lembretes, que não paga quatro serviços a mais num VPS single-node. O próprio
> template manda simplificar quando o componente não é usado.
>
> **O que entra no lugar:** o agendador sai de dentro do container do app e vira
> **serviço próprio do stack** — desacopla o agendador do web, que é a maior parte
> do ganho, sem broker. E o terreno fica preparado para o Celery da Fase 3, a
> custo próximo de zero: entrypoints já separados, três redes já criadas,
> management commands já idempotentes.



# ETAPA 1 — DIAGNÓSTICO DO PROJETO (levantado em 2026-08-02; confirme no código)

## Versões e stack
- Python **3.12** (`Dockerfile`, `python:3.12-slim`) → **mantém**
- Django **5.2.15** LTS (`requirements.txt:2`) → **mantém** (suporte até abr/2028)
- gunicorn 23.0.0 · psycopg[binary] >=3.2,<4 · dj-database-url >=2.2,<3 ·
  whitenoise 6.11.0 · Pillow 12.2.0
- Node 20-alpine só para build do Tailwind (multi-stage no `Dockerfile`)

## Configuração
- `noivas_cia/settings.py` — **arquivo único**, sem django-environ. Lê
  `os.environ.get()` com helpers próprios `_env_bool`, `_env_int`, `_env_list`
  (`settings.py:21-37`). Comportamento alterna por `DJANGO_ENV`.
- `ALLOWED_HOSTS` ← `_env_list('DJANGO_ALLOWED_HOSTS')`, fallback em DEBUG
  (`settings.py:59-65`). `CSRF_TRUSTED_ORIGINS` ← `_env_list`, vazio por padrão
  (`settings.py:227`).
- `SECRET_KEY` obrigatório em produção, senão `ImproperlyConfigured`
  (`settings.py:43-57`).
- **Já implementado:** `SECURE_PROXY_SSL_HEADER` condicional
  (`settings.py:240-241`), `SECURE_REDIRECT_EXEMPT=[r'^healthz/$']`
  (`settings.py:231`), HSTS 1 ano, cookies seguros e `SESSION_COOKIE_HTTPONLY`
  em não-DEBUG (`settings.py:232-238`).
- `TIME_ZONE='America/Sao_Paulo'`, `LANGUAGE_CODE='pt-br'` (hardcoded).
- Middleware stack padrão + WhiteNoise; **nenhum middleware customizado**.

## Banco
- `dj_database_url.parse(DATABASE_URL)` com `conn_max_age=60` e
  `conn_health_checks=True` (`settings.py:131-141`); fallback SQLite só em DEBUG;
  produção sem `DATABASE_URL` levanta `ImproperlyConfigured`.
- PostgreSQL **16-alpine** no `docker-compose.yml`, healthcheck `pg_isready`.

## Assíncrono, cache, e-mail
- **Celery: ausente. RabbitMQ: ausente. Redis: ausente. `CACHES`: ausente.**
- Trabalho em segundo plano hoje: `scripts/report_scheduler.sh`, um laço
  `while true; do python manage.py send_daily_whatsapp_report --if-due; sleep 30;
  done`, disparado em background pelo `docker-entrypoint.sh` dentro do container
  do app. Idempotência garantida por `AuditLog` + `Company.select_for_update()`.
  **Não escala além de 1 réplica e perde execução se o container morrer.**
- Integração externa: Evolution API (WhatsApp) via `EVOLUTION_API_URL`,
  `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE` (`settings.py:159-161`).

## Deploy existente
- `Dockerfile` multi-stage (Node build do Tailwind → Python slim), `EXPOSE 8000`,
  `STOPSIGNAL SIGTERM`, `CMD ["./docker-entrypoint.sh"]`.
- `docker-compose.yml` para dev: db + app, `depends_on: service_healthy`, porta
  publicada só em `127.0.0.1:8000`.
- `docker-entrypoint.sh`: `migrate --noinput` → `collectstatic --noinput` →
  scheduler em background → `exec gunicorn`. **Sem `--clear` no collectstatic,
  sem `wait_for_db`, sem advisory lock.**
- **Não existe:** `.github/workflows/`, `docker-stack.yml`, Traefik, secrets,
  `scripts/deploy.sh`, `scripts/backup.sh`.

## Servidor de aplicação
- Gunicorn WSGI: `gunicorn noivas_cia.wsgi:application --bind 0.0.0.0:${PORT:-8000}
  --workers ${WEB_CONCURRENCY:-3} --timeout ${GUNICORN_TIMEOUT:-60}
  --access-logfile - --error-logfile - --capture-output
  --forwarded-allow-ips ${GUNICORN_FORWARDED_ALLOW_IPS:-127.0.0.1}`.
  Sem `gunicorn.conf.py`, sem `--max-requests`.
- `asgi.py` existe mas não é usado; sem channels, sem websockets.

## Healthcheck
- **`GET /healthz/`** → `core.views.healthz` (`core/urls.py:6`,
  `core/views.py:24-26`): `JsonResponse({'status':'ok'})`, sem banco, sem auth.
  **Já pronto** — o alvo pede `/health/`; manter `/healthz/` e apontar Traefik e
  Docker para a rota existente.

## Estáticos e media
- WhiteNoise `CompressedManifestStaticFilesStorage` em produção
  (`settings.py:200-205`). `STATIC_ROOT` e `MEDIA_ROOT` por env com fallback.
- Media = fotos de comprovante das locações (`RentalItem.proof_photo`), servidas
  por view autenticada. **Dado de cliente: nunca expor o diretório direto.**

## Particularidades
- **Single-tenant** — model singleton `Company`. Sem multi-tenant.
- Auth customizado `accounts.User` (login por e-mail), permissões por módulo e
  ação com mixins em `core/mixins.py`.
- Signals em `rentals/signals.py` (recalcula total) e `movements/signals.py`
  (sincroniza status).
- **783 testes passando** — critério de aceite do upgrade de versão.
- Base de produção: 35.875 locações, 71.946 itens, 55.228 recebíveis, 18.846
  clientes, 10.315 produtos. Histórico de 12 anos importado do legado BRcom.

## O que FALTA para a arquitetura-alvo
1. Traefik (reverse proxy, wildcard TLS via DNS-01 Cloudflare, dashboard com Basic Auth)
2. `docker-stack.yml` no formato Swarm (replicated, update_config, restart_policy, limits)
3. Redes overlay `traefik_public` (external), `noivascia_internal`, `noivascia_egress`
4. Docker Secrets (Cloudflare, Postgres, `DJANGO_SECRET_KEY`, Evolution)
5. `wait_for_db` + advisory lock nas migrations + `collectstatic --clear`
6. Entrypoint separado para serviços que não são web (hoje o scheduler; amanhã
   worker e beat de Celery)
7. Scheduler como serviço próprio do stack, saindo do container do app
8. `scripts/deploy.sh` e `scripts/backup.sh`
9. CI/CD para build e push no GHCR
10. `docs/deploy.md` e MkDocs
11. **Migração da base do EasyPanel para a VPS nova**



# ETAPA 2 — ARQUITETURA DE DEPLOY ALVO

## Orquestração e serviços
- Docker + Docker Compose local; Docker Swarm em produção via `docker stack deploy`.
- Serviços do stack `noivascia`: **app** (Django/Gunicorn), **db** (PostgreSQL 16),
  **traefik** e **scheduler** (laço do `send_daily_whatsapp_report --if-due`,
  hoje embutido no container do app).
- Imagem publicada em `ghcr.io/elvertoni/noivaecia`; deploy com
  `docker stack deploy --with-registry-auth`.
- Volumes nomeados: `postgres_data`, `media_data`, `static_data`, `letsencrypt`.
- Redes overlay:
  - `traefik_public` — external, compartilhada com o Traefik. Apenas **app** e **traefik**.
  - `noivascia_internal` — `internal: true`, sem internet. db, app e scheduler.
  - `noivascia_egress` — overlay com internet, fora do Traefik. O **scheduler**,
    que precisa alcançar a **Evolution API**.
  - Nunca colocar o scheduler na `traefik_public` — não recebe tráfego HTTP.
  - A topologia já acomoda worker e beat de Celery na Fase 3 (`internal` +
    `egress`), e Redis/RabbitMQ só em `internal`, sem redesenho.

## TLS / Traefik / Cloudflare
- Certificado **wildcard** (`noivaseciabandeirantes.com.br` e
  `*.noivaseciabandeirantes.com.br`) via Let's Encrypt com desafio **DNS-01**
  Cloudflare. Não combinar tlschallenge com dnschallenge no mesmo resolver.
- Token de API do Cloudflare com **Zone > DNS > Edit e Zone > Zone > Read**.
  Nunca em texto puro: Docker Secret `CLOUDFLARE_DNS_API_TOKEN`, lido via
  `CF_DNS_API_TOKEN_FILE=/run/secrets/CLOUDFLARE_DNS_API_TOKEN`.
  Ao criar o secret use `printf '%s'`, **nunca `echo`** — o `\n` final invalida o token.
- Redirect http→https e `forwardedHeaders.trustedIPs` com as faixas do Cloudflare.
  Dashboard protegido por Basic Auth (`htpasswd -nbB`, com `$` duplicado para `$$`).
- Com `ALLOWED_HOSTS` restrito, definir
  `loadbalancer.healthcheck.hostname=noivaseciabandeirantes.com.br`. Sem isso o
  Traefik manda o IP interno da task no header Host e o Django responde 400
  DisallowedHost, marcando o backend unhealthy.
- **Rota do healthcheck é `/healthz/`**, não `/health/`.

## Configuração (.env e settings)
- `.env` na raiz (gitignored); o de produção da VPS é separado do de dev. Serviços
  recebem variáveis via `env_file`. Scripts usam **parser seguro** de KEY=VALUE,
  nunca `source`/`.` — valores com `& $ * @` quebram o shell.
- `DJANGO_ALLOWED_HOSTS=noivaseciabandeirantes.com.br,.noivaseciabandeirantes.com.br,localhost,127.0.0.1`
  (localhost e 127.0.0.1 **obrigatórios** para o healthcheck do container).
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://noivaseciabandeirantes.com.br,https://*.noivaseciabandeirantes.com.br`.
- `SECURE_PROXY_SSL_HEADER` e `SECURE_REDIRECT_EXEMPT` **já existem** no
  settings.py — validar, não reescrever.
- Segredos preferem Docker Secrets. Como o settings.py lê de env, adotar a
  convenção `*_FILE`: se `POSTGRES_PASSWORD_FILE` existir, ler o conteúdo do
  arquivo; senão cair na variável. Isso exige um pequeno helper no settings.py.

## Saúde, ordem de subida e migrations
- `/healthz/` já retorna 200 sem banco e sem auth.
- Healthcheck em app (HTTP `/healthz/`) e db (`pg_isready`), com `start_period`
  adequado. O scheduler não expõe porta: sua saúde vem do `restart_policy` e do
  `AuditLog`, que registra cada execução do relatório.
- Swarm ignora `depends_on`: ordem por healthchecks + django command
  **`wait_for_db`** (criar) nos entrypoints.
- Migrations com múltiplas réplicas: entrypoint do **app** aguarda o banco, aplica
  migrations sob **advisory lock do PostgreSQL** e roda `collectstatic --clear`.
  O **scheduler** usa **entrypoint separado** que só aguarda o banco — o mesmo
  que servirá a worker e beat de Celery na Fase 3.

## Resiliência e zero-downtime
- `restart_policy` (on-failure, delay, max_attempts, window) e `resource limits`
  em todos os serviços.
- App com `update_config: order: start-first` e `failure_action: rollback`, mais
  `rollback_config`.
- Gunicorn com `--max-requests` e `--max-requests-jitter` para reciclar workers.

## Scripts
- `scripts/deploy.sh` na VPS: parser seguro do `.env`; valida Swarm ativo, secret
  do Cloudflare, redes `traefik_public` e `noivascia_egress`, `DEBUG=False` e
  localhost em ALLOWED_HOSTS; `git pull`; build e push para o GHCR;
  `docker stack deploy --with-registry-auth`; rollout de app e scheduler.
  Modo `--skip-build`.
- `scripts/backup.sh`: `pg_dump -Fc` + media, com rotação por tempo, e
  **verificação de que o dump abre** (`pg_restore --list`). Um backup não
  verificado não é backup.

## Migração de dados (específico deste projeto)
- A base tem 12 anos de histórico. A virada precisa de: `pg_dump -Fc` da origem,
  cópia **para fora dos dois servidores**, restauração na VPS, conferência de
  contagem por tabela contra a origem, e critério de rollback escrito **antes** de
  começar.
- Conferir também `Company` campo a campo: o `--reset` do importador legado já
  reverteu correção manual do nome da empresa uma vez (2026-08-02).



# ETAPA 3 — ENTREGÁVEL 1: PRD.md
Gere um **PRD.md** na raiz, em markdown, contendo:
1. Visão geral e objetivo do trabalho de deploy.
2. Diagnóstico do estado atual (Etapa 1, reconfirmado no código) e gap analysis.
3. Decisões de arquitetura e componentes condicionais, com justificativa. Aqui,
   Celery/RabbitMQ/Redis **ficam fora** (decisão de 2026-08-02, justificada no
   bloco de escopo acima), assim como multi-tenant e IA. O agendador vira serviço
   próprio do stack.
4. Especificação técnica de cada item, adaptada ao projeto (nomes de serviços,
   variáveis, arquivos a criar/alterar).
5. **Sprints de implementação**, tarefas pequenas, cada uma como checklist `[ ]`,
   dizendo o arquivo afetado e o critério de pronto. Ordenação sugerida:
   (S0) preparação e análise; (S1) Dockerfile + entrypoints + `wait_for_db`;
   (S2) settings/.env e convenção `*_FILE` para secrets; (S3) scheduler como
   serviço próprio, fora do container do app; (S4) docker-compose local;
   (S5) `docker-stack.yml` com healthchecks/restart/limits/secrets/redes/volumes;
   (S6) Traefik + Cloudflare DNS-01 wildcard; (S7) `deploy.sh` e `backup.sh`;
   (S8) migração da base do EasyPanel para a VPS; (S9) validação, hardening e
   desligamento do EasyPanel.
6. Riscos e pontos de atenção: build amd64 vs ARM, perda de dados em volumes,
   rotação de segredos e **janela de indisponibilidade da loja**. Registrar
   também a dívida adiada: o salto para 6.2 LTS em abril/2027.

Regras: não quebre funcionalidades existentes; mudanças idempotentes; mantenha o
padrão de código do projeto (aspas simples, PEP 8, UI em pt-BR, código em inglês);
não exponha segredos; use placeholders onde o valor for específico do ambiente.



# ETAPA 4 — ENTREGÁVEL 2: GUIA DE DEPLOY PASSO A PASSO
Inclua no PRD (ou em `docs/deploy.md`) um **guia completo de deploy do zero numa
VPS Ubuntu**, com cada comando em bloco copiável e explicação curta, cobrindo:
1. Provisionar a VPS: usuário não-root, atualização, firewall, swap, tuning de
   kernel, Docker Engine + Compose plugin.
2. `docker swarm init` e criação das redes overlay.
3. DNS no Cloudflare (A + wildcard) e token de API com escopo DNS + Zone Read;
   Docker Secret `CLOUDFLARE_DNS_API_TOKEN`.
4. `.env` de produção e demais secrets.
5. Login no GHCR e primeiro `docker stack deploy --with-registry-auth` (ou
   `./scripts/deploy.sh`).
6. Verificar emissão do wildcard via DNS-01 nos logs do Traefik e os healthchecks
   ficando `healthy`.
7. **Migração da base**: dump do EasyPanel, transferência, restauração,
   conferência de contagens, rollback.
8. Operação: redeploy, logs, rollout, criar superusuário, rodar comandos no
   container, e troubleshooting:
   - `DisallowedHost` por falta de `localhost`/`127.0.0.1` em ALLOWED_HOSTS.
   - Backend unhealthy + `400` de `Go-http-client` em `/healthz/` por falta de
     `loadbalancer.healthcheck.hostname`.
   - Loop de redirect HTTPS por falta de `SECURE_PROXY_SSL_HEADER`.
   - Relatório de WhatsApp não disparando: conferir se o serviço `scheduler` está
     `running`, se alcança a Evolution API pela rede `noivascia_egress` e o que o
     `AuditLog` registrou para o dia.
   - Certificado não emitido por token errado, por falta do escopo Zone > Read,
     por `\n` no secret (`echo` em vez de `printf '%s'`), ou por combinar
     tlschallenge com dnschallenge.
   - `failed to resolve host 'db'` durante a subida — resolvido por healthchecks +
     `wait_for_db`.
9. Backup/restore e rotação de segredos.
10. **Desligamento do EasyPanel** só depois de a VPS nova rodar estável, com o
    backup da base antiga preservado.

> Há material já levantado em `docs/deploy/guia-vps.md` (provisionamento parcial
> da VPS, com o que está feito e o que falta) e `docs/deploy/runbook-cutover-legado.md`
> (armadilhas do importador legado). **Leia os dois antes de escrever** e reaproveite
> em vez de duplicar.



# FORMATO DA RESPOSTA
Primeiro mostre o diagnóstico da Etapa 1 **reconfirmado no código** (aponte
divergências em relação ao que está registrado acima). Em seguida crie/atualize
`PRD.md` e, se aplicável, `docs/deploy.md`. **Não comece a implementar o código do
deploy ainda** — o objetivo é entregar o PRD com as sprints e o guia. Ao final,
liste os arquivos criados e um resumo do plano.

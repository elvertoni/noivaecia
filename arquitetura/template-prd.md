Vou evoluir o **Noivas & Cia**, um sistema de gestão de locação de trajes de
noiva e festa, desenvolvido com Python 3.12 e o Framework Django 5.2 LTS.

> **Estado atual:** o sistema já existe, está em produção e opera uma loja real
> em Bandeirantes/PR. Hoje roda Python 3.12 + Django 5.2.15, servido por Gunicorn
> atrás do EasyPanel. Base de produção em PostgreSQL 16 com 35.875 locações,
> 18.846 clientes e 10.315 produtos, migrada do sistema legado BRcom (VB6/Access)
> em 2026-07-20 e recarregada em 2026-08-02.
>
> **Objetivo deste PRD:** sair do EasyPanel para uma VPS Ubuntu própria com Docker
> Swarm, mantendo as versões atuais. O agendador continua sem Celery, mas passa a
> ser serviço próprio do stack.
> Repositório: `github.com/elvertoni/noivaecia`.



# TECH SPECS DO SISTEMA
- **Manter Python 3.12 e Django 5.2 LTS.** O template de origem pede Python >3.13
e Django >6.0, o que é correto para projeto novo — quem começa hoje pega a versão
mais recente. Este projeto está em produção numa LTS, e a regra aí é outra: pula
de LTS para LTS.
  Calendário oficial consultado em 2026-08-02: **5.2 LTS tem suporte estendido até
  abril/2028**; o 6.0 vai só até abril/2027 e o suporte mainstream dele **encerra
  em agosto/2026**. Subir para o 6.0 agora *encurtaria* o horizonte de suporte em
  um ano, pagando breaking changes para ficar em situação pior.
  **Próximo passo real:** avaliar o salto 5.2 → **6.2 LTS** quando ele sair, em
  abril/2027 (suporte até abril/2030). Um salto só, com a suíte de 783 testes como
  portão.
- Ambiente virtual em .venv na raiz do projeto. *(Hoje é `venv/`; renomear ou
manter é decisão de baixo impacto — o que não pode é versionar.)*
- requirements.txt sempre atualizado na raiz do projeto.
- **O sistema é single-tenant.** Atende uma única loja, cuja configuração vive no
model singleton `Company` (razão social, CNPJ, taxas de juros/multa, numeração de
contrato, dados do rodapé impresso). **Não implementar multi-tenant** — não há
segunda loja no horizonte e o custo de filtros/middlewares por tenant não se
paga. Se um dia houver, o ponto de entrada é `Company`.
- O sistema de autenticação é o nativo do Django, com `AbstractUser` estendido em
`accounts.User`.
- O login de usuários é feito por **e-mail**, sem username.
- Controle de acesso por módulo e por ação: `accounts.ModulePermission` e
`accounts.ActionPermission`, aplicados via `core.mixins.ModuleAccessMixin`
(`module_key`) e `core.mixins.ActionRequiredMixin` (`action_key`). Nunca
reimplementar checagem por view.
- Middleware de proteção de media: as fotos de comprovante das locações
(`RentalItem.proof_photo`) contêm dado de cliente e **não podem ficar públicas**.
Hoje são servidas por view autenticada; manter esse comportamento na VPS nova —
nunca expor `MEDIA_ROOT` direto pelo Traefik.
- Sistema de disparo de e-mails usando o próprio sistema do Django, com dados no
.env → settings.py.
- As entidades/domains do sistema estão separadas em apps do Django. **Apps
existentes**, todos na raiz: `core` (principal — `TimeStampedModel`, mixins,
dashboard, template tags), `accounts`, `company`, `customers`, `catalog`,
`rentals`, `movements`, `billing`, `reports`, `maintenance`, `notifications`,
`website`. **Não criar um app `base`** — o papel de "recursos base e
compartilhados" já é do `core`; duplicar isso quebraria imports em todo o projeto.
- O código deve ser simples, sempre usar aspas simples e seguir a PEP 8.
- O código do projeto é em inglês; **toda a interface é em português brasileiro**.
Timezone `America/Sao_Paulo`, `LANGUAGE_CODE = 'pt-br'`.
- Toda tabela/model herda `core.models.TimeStampedModel`, que fornece `created_at`
e `updated_at`.
- **Manter e expandir a suíte de testes.** O projeto tem 783 testes passando e
eles são o que sustenta a troca de servidor e, mais adiante, o salto para o
6.2 LTS. Não remover testes.
- Credenciais em `.env` na raiz (gitignored), importadas no settings.py.
- Manter apenas 1 arquivo settings.py, com o comportamento alternado por
`DJANGO_ENV` (development|production).
- Banco de dados **PostgreSQL 16**.
- Docker + Docker Compose para rodar localmente; Docker Swarm para o deploy na
VPS Ubuntu, com domínio gerenciado no Cloudflare (domínio: `noivaseciabandeirantes.com.br`).
- **Celery, RabbitMQ e Redis ficam FORA deste ciclo** — decisão de 2026-08-02, com
justificativa. O template de origem exige Celery para "processamento do agente de
IA principalmente"; como não há IA neste sistema, o requisito perde a razão de
ser. O trabalho assíncrono aqui é **um relatório diário de WhatsApp e lembretes de
retirada/devolução** — carga que não paga quatro serviços a mais num VPS
single-node, cada um com healthcheck, credencial e modo de falha próprios
(RabbitMQ sozinho ocupa 100–200 MB ociosos). O próprio template manda simplificar
quando o projeto não usa o componente.
  **Entra quando houver carga que justifique** — geração de PDF em lote, disparo
  de mensagens em massa, importação pesada. Até lá, ver a preparação abaixo.
- **Agendador:** o `send_daily_whatsapp_report --if-due` continua rodando em laço,
mas como **serviço próprio no stack**, não mais dentro do container do app
(`docker-entrypoint.sh` hoje o dispara em background). Isso desacopla o agendador
do web — que é a maior parte do ganho que o Celery traria — sem introduzir broker
nenhum. A idempotência já está garantida por `AuditLog` +
`Company.select_for_update()`, então o serviço tolera reinício e réplica dupla
momentânea durante um rollout `start-first`.
- **Preparar o terreno para o Celery sem pagar por ele agora** (custo próximo de
zero, evita retrabalho na Fase 3):
  - entrypoints já separados: `entrypoint-app.sh` (migrations + collectstatic) e
    `entrypoint-worker.sh` (só `wait_for_db`) — o segundo já serve ao agendador;
  - as três redes overlay já criadas, incluindo a de saída;
  - management commands mantidos idempotentes, para virarem task sem reescrita.
- Os serviços Docker devem ser: **app** (django), **db** (postgresql),
**scheduler** (agendador do WhatsApp) e **traefik** como web server/load balancer.
- A imagem da aplicação deve ser publicada em **`ghcr.io/elvertoni/noivaecia`**, e
o deploy do stack no Swarm deve usar `docker stack deploy --with-registry-auth`.
Nome do stack: **`noivascia`**. Devem existir volumes nomeados para persistência
(postgresql, media, staticfiles e certificados do Let's Encrypt). As redes overlay
devem ser três: uma pública (`traefik_public`, external, compartilhada com o
Traefik, usada apenas por app e traefik), uma interna isolada
(`noivascia_internal`, `internal: true`, sem acesso à internet, para db, app e
scheduler) e uma de saída (`noivascia_egress`, overlay sem `internal`, com acesso
à internet mas fora do Traefik, para os serviços que chamam APIs externas).
O **scheduler** precisa de saída para a **Evolution API do WhatsApp**, então fica
em `noivascia_internal` + `noivascia_egress`. O app fica em `traefik_public` +
`noivascia_internal`. O db fica só em `noivascia_internal`. Nunca colocar o
scheduler na `traefik_public` — ele não recebe tráfego HTTP.
*(As três redes já contemplam worker e beat de Celery quando chegarem: entram em
`internal` + `egress`, sem mudar a topologia. Redis e RabbitMQ, se vierem, ficam
só em `internal`.)*
- O Traefik deve emitir certificado TLS wildcard (cobrindo `noivaseciabandeirantes.com.br` e
`*.noivaseciabandeirantes.com.br`) via Let's Encrypt usando o challenge DNS-01 com o provider
Cloudflare. É necessário um token de API do Cloudflare com escopo DNS
(Zone > DNS > Edit) **e também Zone > Zone > Read** na zona `noivaseciabandeirantes.com.br`. O
challenge DNS-01 é obrigatório para wildcard; não usar tlschallenge e dnschallenge
ao mesmo tempo no resolver.
- O token da API do Cloudflare nunca em texto puro no compose/stack nem no .env
versionado: armazenar como Docker Secret do Swarm (nome
`CLOUDFLARE_DNS_API_TOKEN`) e ler no Traefik via
`CF_DNS_API_TOKEN_FILE=/run/secrets/CLOUDFLARE_DNS_API_TOKEN`. As demais
credenciais sensíveis de produção — senha do PostgreSQL, `DJANGO_SECRET_KEY` e
`EVOLUTION_API_KEY` — também devem usar Docker Secrets.
- Credenciais e variáveis em `.env` na raiz (gitignored). O `.env` de produção da
VPS é separado do de desenvolvimento. Os serviços recebem as variáveis via
`env_file` (lido direto pelo Docker, sem shell). Qualquer script que precise ler o
`.env` deve usar parser seguro de KEY=VALUE (nunca `source`/`.`), pois valores com
`& $ * @` quebram o shell.
- `ALLOWED_HOSTS` e `CSRF_TRUSTED_ORIGINS` lidos do .env como listas separadas por
vírgula. Padrão de produção:
`DJANGO_ALLOWED_HOSTS=noivaseciabandeirantes.com.br,.noivaseciabandeirantes.com.br,localhost,127.0.0.1` (o ponto inicial
cobre subdomínios; localhost e 127.0.0.1 são obrigatórios para o healthcheck
interno do container passar) e
`DJANGO_CSRF_TRUSTED_ORIGINS=https://noivaseciabandeirantes.com.br,https://*.noivaseciabandeirantes.com.br` (sempre com
esquema). Em ALLOWED_HOSTS vai apenas o hostname, nunca a URL.
- Em produção (DEBUG=False), com TLS terminado no Traefik e o app recebendo HTTP
interno com `X-Forwarded-Proto`, o settings.py já configura
`SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')` e isenta a rota de
healthcheck via `SECURE_REDIRECT_EXEMPT`. **Já implementado** — validar, não
reescrever. O Traefik deve confiar nas faixas de IP do Cloudflare
(`forwardedHeaders.trustedIPs`) e redirecionar http→https.
- O app expõe healthcheck em **`/healthz/`** (`core.views.healthz`), que retorna
`{"status":"ok"}` sem tocar o banco e sem exigir autenticação. **Já
implementado.** *(O template original pede `/health/`; manter `/healthz/` evita
mexer em healthcheck que já funciona — apenas apontar Traefik e Docker para a rota
existente.)*
- Todos os serviços do Swarm devem ter healthcheck: app (HTTP em `/healthz/`) e
postgresql (`pg_isready`), com `start_period` adequado. O scheduler não expõe
porta; sua saúde é o próprio `restart_policy` mais o `AuditLog`, que registra cada
execução.
Como o Swarm ignora `depends_on` em runtime, a ordem de subida é garantida por
healthchecks somados a um django command `wait_for_db` nos entrypoints.
- As migrations devem rodar com segurança mesmo com múltiplas réplicas: o
entrypoint do app aguarda o banco (`wait_for_db`), aplica migrations usando
**advisory lock do PostgreSQL** (só uma réplica migra por vez) e roda
`collectstatic --clear`. O **scheduler** usa entrypoint separado que apenas
aguarda o banco e **não** roda migrations nem collectstatic — mesmo entrypoint que
servirá a worker e beat de Celery na Fase 3.
- Deve existir `scripts/deploy.sh` executado na própria VPS: carrega o .env com
parser seguro, valida pré-condições (Swarm ativo, secret `CLOUDFLARE_DNS_API_TOKEN`,
redes `traefik_public` e `noivascia_egress`, `DEBUG=False` e localhost em
ALLOWED_HOSTS), faz git pull, build e push da imagem para o GHCR, executa
`docker stack deploy --with-registry-auth` e força o rollout de app, worker e beat.
Modo `--skip-build` para redeploy sem rebuild. Deve existir também
`scripts/backup.sh` do PostgreSQL e da media, com rotação por tempo.
**O backup é crítico:** a base carrega 12 anos de histórico do sistema legado.
- Sempre que possível, usar Class Based Views e recursos nativos do Django. O
projeto já é majoritariamente CBV.
- Signals ficam em `signals.py` dentro da app correspondente. Já é o padrão:
`rentals/signals.py` recalcula o total da locação, `movements/signals.py` sincroniza
o status a partir de retirada/devolução.
- **Impressão de contrato:** o contrato sai de um template HTML
(`templates/rentals/rental_contract.html`) impresso pelo navegador, com duas vias
em uma folha A4. **Não migrar para Reportlab/PyPDF sem necessidade** — o layout
atual é resultado de ajustes finos com a cliente e cabe 15 itens por via, medido.
Reportlab/PyPDF só se surgir relatório que o HTML não resolva.
- Pasta `docs/` com documentação sempre atualizada, servida com MkDocs, incluindo
suporte a mermaid. *(A pasta já existe com material de migração, deploy e
arquitetura; falta o MkDocs.)*
- Um django command de carga inicial de dados fake, cobrindo múltiplos cenários e
datas, para demonstração. *(Não existe hoje; o `core/import_legacy_access` carrega
dado real do legado, o que não serve para demo.)*
- **Design system:** o projeto usa TailwindCSS 3.4 com tokens definidos em
`tailwind.config.js`, compilados de `static/src/input.css` para
`static/css/output.css` (gerado, nunca versionado). Todo design deve respeitar
esses tokens. *(Não existe `design_system/design-system.html`; a fonte da verdade
é o `tailwind.config.js` mais as convenções de grade em `AGENTS.md`.)*
- **Não há agentes de IA no sistema.** Langchain, Langgraph e integração com LLM
estão fora de escopo — o produto é gestão de locação, e nenhum requisito atual
pede geração ou análise por IA.



# REQUISITOS FUNCIONAIS DO SISTEMA

Domínio: uma **locação** (`Rental`, numerada sequencialmente por
`Company.next_rental_number()`) pertence a um **cliente** e contém **itens**
(`RentalItem`) que referenciam **produtos** do catálogo. Ela gera **recebíveis**
(`Receivable`) no financeiro e recebe **retirada** (`Pickup`) e **devolução**
(`Return`) em movimentações, que sincronizam o status da locação via signals.

- **RF-01 Clientes:** cadastro com CPF ou CNPJ, endereço, telefones, busca
normalizada por nome/documento. Detecção e mesclagem de duplicados (a base legada
trouxe 518 CPFs repetidos).
- **RF-02 Catálogo:** categorias com prefixo e produtos com código, descrição,
cor, tamanho e disponibilidade por período. O preço **não** fica no cadastro — é
digitado no ato da locação.
- **RF-03 Locação:** grade densa de itens, teclado em primeiro lugar (`Enter`
navega, `F2` adiciona linha, `Ctrl+S` salva). Máximo de **15 peças** e **entrada
obrigatória + até 8 parcelas** — limites medidos no contrato impresso. Desconto à
vista em reais. Campo "valor de reposição" (`penalty_value`) com o custo de repor
as peças, impresso na cláusula 3 do contrato.
- **RF-04 Contrato impresso:** duas vias em uma folha A4, com itens, condições de
pagamento, termos e assinaturas. Validação obrigatória gerando o PDF pela
aplicação em execução, nunca por download antigo.
- **RF-05 Movimentação:** registro de retirada e devolução, com dias de atraso e
multa; sincroniza `Rental.status`.
- **RF-06 Financeiro:** parcelas, recebimentos, juros e multa centralizados em
`billing/services.py` (`compute_interest`, `register_payment`, `reverse_payment`,
`financial_kpis`). Vocabulário voltado ao cliente usa "receber/recebimento/
recebido", nunca "pagar/pagamento/pago".
- **RF-07 Relatórios:** acompanhamento operacional e financeiro, somente leitura.
- **RF-08 Notificações WhatsApp:** relatório diário e lembretes de retirada e
devolução via Evolution API, com um `CustomerMessage` por tentativa (inclusive
falhas, para não renotificar em retry). Templates de mensagem centralizados.
- **RF-09 Manutenção:** rotinas administrativas protegidas (reconciliação de
saldos, limpeza de duplicados, relatórios de homologação).
- **RF-10 Site institucional:** página pública.

**Dívida conhecida, a resolver com a cliente antes de virar requisito:**
`movements.services.compute_penalty` cobra o `penalty_value` inteiro como multa de
atraso na devolução. Como esse campo é o custo de reposição das peças (1,2x a 3x o
valor da locação), um dia de atraso gera um recebível do preço de repor tudo.



# REQUISITOS NÃO FUNCIONAIS DO SISTEMA
- Responsivo em todos os tamanhos de tela. A grade de locação empilha abaixo do
breakpoint `sm` e **nunca** usa scroll vertical aninhado — a página é dona do
scroll.
- Seguro: rotas fechadas por módulo e ação, sem exposição de dado sensível. As
fotos de comprovante e os dados de cliente (CPF/CNPJ, endereço, telefone) só
podem ser acessados por usuário autenticado com permissão. **LGPD:** mídia de
cliente (vídeos, áudios, prints) e dumps de produção nunca vão para o
repositório — hoje garantido por `.gitignore` em `correcoes/`, `backups/` e
`db-migration/`.
- UI/UX com base nos tokens do Tailwind, pensada na fluidez da operação de balcão.
Bom contraste entre elementos, fontes e fundo.
- Tarefas em segundo plano não bloqueiam: o usuário vê loading no botão e aviso de
que será notificado, com notificação na interface ao concluir.
- Bom desempenho de filtros e telas sobre uma base de 35 mil locações, 55 mil
recebíveis e 18 mil clientes. Índices já declarados em `Rental._meta.indexes`.
- Deploy em Swarm resiliente: `restart_policy` (on-failure, com delay,
max_attempts e window) e `resource limits` (limits e reservations de CPU e
memória) em todos os serviços, para evitar starvation na VPS.
- Atualização do app sem downtime: `update_config` com `order: start-first` e
`failure_action: rollback`, mais `rollback_config`.
- Subida ordenada e auto-recuperável: nenhum serviço em crash-loop por dependência
não pronta — garantido por healthchecks, `wait_for_db` nos entrypoints e
`restart_policy` com delay.
- O scheduler precisa de saída para a Evolution API: fica em `noivascia_egress`
além de `noivascia_internal`, nunca em `traefik_public`. A separação em três redes
segue o princípio de menor privilégio e já acomoda worker e beat de Celery quando
chegarem.
- No `collectstatic` do entrypoint, sempre usar `--clear`, para evitar
`FileNotFoundError` por arquivo hash obsoleto do WhiteNoise
`CompressedManifestStaticFilesStorage` em redeploys.
- Segredos de produção nunca em texto puro em arquivo versionado: Docker Secrets
e/ou `.env` gitignored da VPS.
- **Preservação de dados na virada:** a base tem 12 anos de histórico importado do
legado. Backup verificado e copiado para fora da VPS **antes** de qualquer
operação destrutiva, com restauração testada.



# TAREFA
Gere o PRD desse projeto (Product Requirement Document), em formato markdown. O
PRD será usado como guia do projeto no desenvolvimento. Coloque todos os detalhes
necessários tanto técnicos quanto de planejamento. Adicione também uma sessão com
guia de deploy do sistema em uma VPS Ubuntu do zero, cada comando e passo a passo
detalhado para deploy em docker com swarm. Esse guia deve incluir: instalação do
Docker e inicialização do Swarm (`docker swarm init`), criação das redes overlay
(`traefik_public` external e as internas), criação do token de API do Cloudflare
(escopo DNS + Zone Read na zona `noivaseciabandeirantes.com.br`) e do Docker Secret correspondente
(`CLOUDFLARE_DNS_API_TOKEN`), configuração do `.env` de produção (DEBUG=False,
ALLOWED_HOSTS e CSRF_TRUSTED_ORIGINS no padrão definido), criação dos demais
secrets, deploy do stack com healthchecks e restart policies, verificação da
emissão do certificado wildcard via DNS-01 e o uso do `scripts/deploy.sh`.

**Inclua obrigatoriamente uma sessão de migração de dados**, cobrindo como levar a
base PostgreSQL do EasyPanel para a VPS nova sem perda: dump verificado, cópia
para fora dos dois servidores, restauração, conferência de contagens por tabela e
critério de rollback.

Adicione no PRD uma sessão com as sprints de implementação, com tarefas pequenas e
bem detalhadas, em ordem lógica. As sprints e tarefas devem ter o espaço `[ ]`
para marcação de `[x]` quando concluídas, em forma de checklist. Cada tarefa deve
dizer o arquivo afetado e o critério de pronto.



# IMPORTANTE
Para construir o PRD.md e todo seu escopo de definições técnicas, estrutura do
projeto, padrões, guidelines, stack, arquitetura e planejamento, use como base e
template o projeto SCSI (https://github.com/pycodebr/scsi, branch main),
replicando o mais fielmente possível a arquitetura e padrões daquele projeto —
**naquilo que se aplica**. O Noivas & Cia é um sistema já existente e em produção:
onde o SCSI e o código atual divergirem, **o código atual vence** em tudo que já
funciona e foi validado com a cliente. O SCSI é referência para a camada de
infraestrutura e deploy (Swarm, Traefik, secrets, entrypoints, scripts),
não para reescrever o domínio.

**Não quebre funcionalidades existentes.** Mudanças idempotentes, mantendo o
padrão de código do projeto, sem expor segredos, com placeholders onde o valor for
específico do ambiente.

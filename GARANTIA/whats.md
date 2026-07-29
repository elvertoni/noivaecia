# Plano — Relatórios diários via WhatsApp (Evolution API)

Objetivo: o sistema envia todo dia, no WhatsApp da administradora **Ana**, um
resumo operacional com três blocos: **entregas a fazer** (devoluções previstas
para hoje), **locações a retirar** (retiradas previstas para hoje + atrasadas)
e **valores a receber** (títulos vencendo hoje + vencidos em aberto).

Escopo da fase 1: mensagem **somente para a Ana** (1 número, configurável).
Envio a clientes fica para fase futura (seção 9), reaproveitando a mesma
infraestrutura.

---

## 1. Arquitetura e decisões

1.1. **Evolution API já está no ar** no EasyPanel, projeto `work`, serviço
     `evolution-api` (imagem `evoapicloud/evolution-api:latest`), com Postgres
     17 e Redis próprios. Não há nada para provisionar de infra.

1.2. **Comunicação interna, não pública**: o app `noivaecia` e o
     `evolution-api` estão no mesmo projeto/rede Swarm. O Django chama
     `http://work_evolution-api:8080` direto — a API key nunca trafega pela
     internet e o endpoint público do Evolution não precisa ser exposto ao app.

1.3. **Sem dependência nova**: cliente HTTP com `urllib.request` da stdlib
     (convenção do projeto: nenhuma dependência sem justificativa; é 1 POST
     JSON com timeout — não justifica `requests`).

1.4. **Novo app Django `notifications`** (um domínio de negócio novo, segue o
     padrão de 1 app por domínio do CLAUDE.md):
     - `notifications/evolution.py` — cliente fino da Evolution API.
     - `notifications/services.py` — montagem dos relatórios (reusa
       `reports/services.py` e `billing/services.py`, nunca duplica query).
     - `notifications/management/commands/send_daily_whatsapp_report.py`.
     - testes correspondentes.

1.5. **Configuração em dois níveis**:
     - Infra (env vars no serviço `noivaecia`): `EVOLUTION_API_URL`,
       `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE`. Chave já existe no painel
       (env `AUTHENTICATION_API_KEY` do serviço `evolution-api`) — copiar de
       lá, nunca commitar.
     - Negócio (singleton `Company`, editável na tela Empresa):
       `whatsapp_reports_enabled` (bool), `whatsapp_report_number` (telefone
       da Ana, formato E.164 `5543...`), `whatsapp_report_time` (hora do
       envio, default 07:30).

1.6. **Agendamento dentro do container** do app (replicas=1, sem risco de
     duplicidade): processo `scheduler` iniciado pelo `docker-entrypoint.sh`
     ao lado do gunicorn — loop shell de 60s que dispara o management command
     quando `HH:MM` bate com o horário configurado. Zero dependência externa
     (sem celery, sem n8n no caminho crítico). `TIME_ZONE` já é
     `America/Sao_Paulo`, o loop usa a hora local do Django.

1.7. **Auditoria e idempotência**: cada envio grava `AuditLog`
     (`action='whatsapp_daily_report'`) com data de referência, número
     destino e id da mensagem retornado pelo Evolution. O comando recusa
     reenvio no mesmo dia (a menos que `--force`), então restart do container
     não duplica mensagem.

1.8. **Falha não derruba nada**: erro de envio loga `AuditLog`
     (`whatsapp_send_failed`) + stderr e sai com código ≠ 0; a operação da
     loja nunca depende do WhatsApp.

## 2. Pré-requisitos manuais (painel/celular — fazer antes do código)

2.1. Criar a instância no Evolution API (`POST /instance/create`, header
     `apikey`) com nome `noivascia` — ou pelo manager web
     (`https://<domínio-do-evolution>/manager`).

2.2. Parear o número que será o **remetente** (recomendado: número comercial
     da loja, não o pessoal da Ana) escaneando o QR code da instância.

2.3. Confirmar estado `open` da instância (`GET /instance/connectionState/noivascia`).

2.4. Anotar o número da Ana com DDI/DDD (`55DDDNÚMERO`).

2.5. Adicionar no serviço `noivaecia` (EasyPanel → Environment):
     `EVOLUTION_API_URL=http://work_evolution-api:8080`,
     `EVOLUTION_API_KEY=<copiar do painel>`, `EVOLUTION_INSTANCE=noivascia`.

## 3. Fase 1 — Configuração (settings + Company)

3.1. `noivas_cia/settings.py`: ler as 3 env vars (default vazio; feature
     desligada se ausentes).

3.2. `company/models.py`: adicionar os 3 campos de negócio (1.5) +
     migration. Default `whatsapp_reports_enabled=False` — deploy não muda
     comportamento até ligar na tela.

3.3. `company/forms.py` + template da Empresa: expor os campos novos
     (UI em pt-br: "Relatório diário por WhatsApp", "Número do destinatário",
     "Horário do envio").

3.4. Testes: migration aplica, form salva/valida número (só dígitos, 12–13
     dígitos com DDI 55).

## 4. Fase 2 — Cliente Evolution (`notifications/evolution.py`)

4.1. `send_text(number, text)` → `POST {EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}`,
     headers `apikey` + `Content-Type: application/json`, body
     `{"number": number, "text": text}`, timeout 15s.

4.2. Retorna id da mensagem; levanta `EvolutionError` em HTTP ≠ 2xx/timeout,
     com corpo da resposta na mensagem (sem vazar a API key em log).

4.3. `get_connection_state()` para diagnóstico (usado pelo comando com
     `--check`).

4.4. Testes com `unittest.mock` no `urlopen` — nenhum teste toca rede.

## 5. Fase 3 — Montagem do relatório (`notifications/services.py`)

5.1. `build_daily_report(on_date)` retorna o texto final. Três blocos, todos
     com contagem e itens (limitados a ~15 linhas/bloco, com "+N outros"):

     - **Entregas a fazer (devoluções de hoje)**: `Rental` status
       `picked_up` com `return_date == hoje` (+ atrasadas:
       `return_date < hoje`, marcadas ⚠️). Fonte: mesma base de
       `reports/services.report_retirados`/`report_atrasados`.
     - **Locações a retirar**: `Rental` status `pending` com
       `pickup_date == hoje` (+ atrasadas `pickup_date < hoje` ⚠️ — mesma
       regra da aba "A retirar"/badge urgente de `catalog/availability.py`).
     - **Valores a receber**: `Receivable` em aberto (`balance > 0`, sem
       write-off) com `due_date == hoje`, + total vencido acumulado (usar
       `billing/services.py` — nunca recalcular juros fora dele).

5.2. Formato da mensagem (WhatsApp aceita `*negrito*`/`_itálico_`):

     ```
     🗓️ *Noivas & Cia — resumo de seg, 21/07*

     📦 *Entregas a fazer hoje: 3*
     • #56458 Maria Silva — vestido VF-102 (retirado 18/07)
     • ...
     ⚠️ 2 devoluções atrasadas

     👗 *Retiradas de hoje: 5*
     • #56471 Ana Costa — 14h
     • ...
     ⚠️ 1 retirada atrasada

     💰 *A receber hoje: R$ 1.240,00 (4 títulos)*
     • #56430 Carla Souza — R$ 300,00
     Vencidos em aberto: R$ 2.180,00 (7 títulos)
     ```

5.3. Valores monetários com o formatador BRL já existente do projeto.

5.4. Dia sem nada: mensagem curta "✅ Sem entregas, retiradas ou vencimentos
     hoje." — Ana sabe que o sistema está vivo.

5.5. Testes: fixtures com locações/títulos em cada situação; asserts no texto
     (contagens, atrasados, truncamento, dia vazio).

## 6. Fase 4 — Management command

6.1. `send_daily_whatsapp_report`, flags: `--dry-run` (imprime o texto, não
     envia — padrão do projeto), `--force` (ignora trava de reenvio do dia),
     `--to 55...` (override do destino p/ teste), `--check` (só estado da
     conexão), `--date YYYY-MM-DD` (referência p/ teste).

6.2. Fluxo: feature ligada? → horário já chegou? → remove destinatários já
     enviados hoje (AuditLog) → monta texto → envia os pendentes → grava
     AuditLog por destinatário com message id. Falhas são tentadas novamente
     após 5 minutos, sem repetir os destinos que já tiveram sucesso.

6.3. Testes: trava de reenvio, dry-run sem HTTP, force, feature desligada
     sai silencioso com aviso.

## 7. Fase 5 — Agendador no container

7.1. `scripts/report_scheduler.sh`: loop `while true; sleep 30`; o próprio
     comando compara a hora local com `whatsapp_report_time` e roda
     `send_daily_whatsapp_report --if-due`. Antes do horário ele sai rápido;
     depois do horário envia somente destinatários ainda pendentes. Assim,
     deploys, reinícios e números adicionados após o horário recuperam o envio
     no mesmo dia.

7.2. `docker-entrypoint.sh`: iniciar o scheduler em background antes do
     `exec gunicorn` (`sh scripts/report_scheduler.sh &`). Logs no stdout do
     container (prefixo `[scheduler]`).

7.3. `Dockerfile`: garantir `scripts/` copiado e executável (já cobre via
     `COPY . .` + chmod no entrypoint).

7.4. Não há janela única de 1 minuto: o scheduler faz catch-up durante o dia.
     A trava de idempotência por data e destinatário impede duplicidade; uma
     falha recente aguarda 5 minutos antes de nova tentativa.

## 8. Fase 6 — Deploy e validação em produção

8.1. Pré-commit checklist do CLAUDE.md + suíte completa local.

8.2. Commit/push (Conventional Commits, ex.:
     `feat(notifications): daily WhatsApp report via Evolution API`).

8.3. Env vars no painel (2.5) **antes** do deploy.

8.4. Deploy (rolling voltou a funcionar após remoção da porta 55432).

8.5. Validação em ordem, dentro do container:
     1. `manage.py send_daily_whatsapp_report --check` → `open`.
     2. `--dry-run --date <hoje>` → conferir texto com dados reais.
     3. `--to <número de teste>` → recebe no seu celular.
     4. Ligar `whatsapp_reports_enabled` na tela Empresa com o número da Ana.
     5. Dia seguinte: confirmar recebimento no horário + `AuditLog`.

8.6. Rollback: desligar o toggle na tela Empresa (ou remover env vars) —
     nenhuma migração destrutiva envolvida.

## 9. Fases futuras (fora do escopo, projetadas desde já)

9.1. Aviso ao cliente na véspera da retirada/devolução (exige opt-in LGPD no
     cadastro do cliente + template de mensagem por evento).

9.2. Cobrança amigável de título vencido (integra com `compute_interest`).

9.3. Confirmação de devolução recebida ("obrigado, tudo certo!").

9.4. Webhook do Evolution → registrar respostas da Ana (ex.: "ok") no
     AuditLog.

9.5. Segundo destinatário (dona da loja) — trocar campo único por lista.

## 10. Riscos e mitigação

10.1. **Número banido pelo WhatsApp** (Evolution usa API não-oficial/Baileys):
      mitigar usando número comercial dedicado, 1 mensagem/dia, sem spam a
      clientes na fase 1. Aceito pelo baixo volume.

10.2. **Instância desconecta** (celular offline/QR expira): `--check` diário
      no início do comando; falha gera `AuditLog whatsapp_send_failed` e
      registro no log do container. Evoluir depois p/ alerta por e-mail
      (settings de e-mail já existem no `.env`).

10.3. **API key no ambiente do app**: fica só em env var do EasyPanel (mesmo
      tratamento do `DATABASE_URL`); comunicação é interna à rede Swarm.

10.4. **Fuso/DST**: `TIME_ZONE='America/Sao_Paulo'` já configurado; scheduler
      usa hora local do Django, não UTC.

## 11. Critérios de aceite (fase 1 concluída quando)

11.1. Ana recebe o resumo diário no horário configurado, com os 3 blocos
      corretos contra os dados de prod.

11.2. Restart do container não duplica nem perde o envio do dia.

11.3. Toggle na tela Empresa liga/desliga sem deploy.

11.4. Suíte de testes cobre builder, cliente (mock), comando e trava de
      idempotência; zero testes tocando rede.

11.5. Nenhuma query de negócio duplicada — relatório consome
      `reports/services.py` e `billing/services.py`.

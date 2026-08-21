# Runbook — cutover do sistema legado BRcom para produção

**Escopo:** virada definitiva em que a loja para de usar o BRcom (VB6/Access) e passa a
operar só no Noivas & Cia. Complementa `guia-vps.md` §12 (migração de infra) — este
documento trata da **migração de dados**.

**Regra que vale mais que qualquer script:** o momento em que os dois sistemas aceitarem
escrita ao mesmo tempo é o momento em que você perde a capacidade de saber qual é a
verdade. Todo o resto aqui é mecânico.

---

## 1. Decisão registrada: re-import completo, não delta

Quando chegar a hora, a tentação é importar "só os últimos 15 dias" para encurtar a
janela. **Não faça.** A decisão é fazer `import_legacy_access --reset --confirm-reset`
completo no momento do cutover.

### Por que o delta não funciona

1. **O importador não suporta.** `core/management/commands/import_legacy_access.py`
   (`_ensure_empty_business_tables`) aborta se as tabelas de negócio tiverem dados.
   Existem exatamente dois modos: tabela vazia, ou `--reset --confirm-reset`. Não há
   `--since` nem upsert incremental. Delta exigiria código novo e não testado no caminho
   que grava em produção.
2. **Não é possível detectar "o que mudou".** O Access legado não tem coluna de
   modificação confiável. Um recebível criado há dois anos que recebeu pagamento
   anteontem **não é novo** — ele mudou. Filtrar por data de criação perde exatamente
   esse caso, que é o mais comum na operação financeira.
3. **Integridade referencial.** Uma locação recente aponta para cliente e produtos
   antigos. Delta obrigaria a caminhar as dependências para trás; sem isso o importador
   cria clientes/produtos placeholder e o cadastro fica sujo.
4. **Numeração.** `Rental.number` é `unique=True`. Dois emissores de número em paralelo
   colidem.

### Por que o re-import completo é seguro e barato

- **IDs legados viram PKs do Django** (`id=legacy_id` no importador), então reimportar é
  idempotente por construção — não gera duplicata.
- **`last_rental_number=max(legacy_last, imported_last)`** é recalculado no import, então
  `Company.next_rental_number()` continua de onde o legado parou. Sem colisão.
- **`_reset_sequences()`** corrige as sequences do Postgres depois dos IDs explícitos.
- **`--dry-run`** roda tudo em transação e faz rollback. Pode ensaiar quantas vezes quiser.
- O import completo roda em segundos, não em horas.

---

## 2. Sequência do cutover

1. **Janela de movimento zero com bloqueio técnico** — loja fechada e escrita pública
   em manutenção. Na VPS, mantenha uma task do app apenas para administração, retire a
   rota pública e pare o scheduler:

   ```bash
   cd ~/noivaecia
   docker service scale noivaecia_scheduler=0
   docker service update --replicas 1 \
     --label-add 'traefik.http.routers.noivascia.rule=Host(`maintenance.invalid`)' \
     noivaecia_app
   docker service ps noivaecia_app
   ```

   Confirme externamente que o domínio não entrega mais as telas e que existe exatamente
   uma task do app; use somente esse container para os comandos administrativos. Ao fim,
   `./scripts/deploy.sh --skip-build` restaura labels e réplicas do stack. Nunca faça no
   meio de um dia de atendimento.
2. **Backup do Postgres de produção antes de qualquer coisa:**

   ```bash
   cd ~/noivaecia
   BACKUP_DIR="$HOME/backups" ./scripts/backup.sh
   ```

   O script deve gerar `db_<timestamp>.sql.gz` e `media_<timestamp>.tar.gz`.
   Valide que o `.sql.gz` não está vazio e passa em `gzip -t`; gere SHA-256, copie o
   banco e a mídia para fora da VPS e confira os mesmos hashes no destino. Para dumps
   custom (`pg_dump -Fc`), valide também com `pg_restore --list`. Só então prossiga.
   Este é o ponto de retorno.
3. **A loja para de usar o BRcom. Parada rígida, anunciada com antecedência.**
   A partir daqui o legado é somente leitura.
4. **Export fresco do `.mdb`** — `tools/legacy_migration/export_access.ps1`
   (PowerShell 32-bit, Jet 4.0). Confirme no manifest que o `.mdb` é o do dia, não uma
   cópia velha.
5. **Salvar a configuração operacional da `Company`** antes da carga. A whitelist exata
   é: `name`, `address`, `city`, `cnpj`, `phones`, `daily_interest_rate`,
   `late_fee_rate`, `monthly_interest_rate`, `damage_penalty_rate`,
   `loss_penalty_rate`, `cancellation_penalty_rate`, `late_return_daily_rate`,
   `late_return_max_days`, `footer_message`, `whatsapp_reports_enabled`,
   `whatsapp_report_number` e `whatsapp_report_time`. Guarde esses valores em arquivo
   protegido (`chmod 600`) sem expô-los nos logs. Registre `last_rental_number`
   separadamente. Nunca restaure cegamente `id`, `created_at` ou `updated_at`.
6. **`import_legacy_access --reset --confirm-reset --dry-run`** primeiro. Compare as
   contagens com o resumo do import anterior — clientes, produtos, locações,
   recebíveis e movimentos financeiros. Divergência grande para baixo significa
   export truncado: pare e investigue.
7. **`import_legacy_access --reset --confirm-reset`** para valer.
8. **Conferir as decisões de código duplicado.** O Access carrega ~40 códigos com
   mais de um cadastro, e o importador resolve cada um mantendo ativo o de menor
   `legacy_id` (é a linha à qual todo `RentalItem` fica ligado, porque `locado`
   referencia por `prefixo+codigo`, não por id de produto). Os demais entram
   **anulados**, não excluídos.
   - Leia `duplicate_products_retired` e `duplicate_products_detail` no resumo do
     import — ficam gravados em `legacy_import_audit`.
   - `python manage.py dedupe_product_codes` (dry-run) para ver a triagem.
   - **Avise a cliente:** cerca de 40 peças reais saem da busca até ela dizer
     quais ficam. Nenhuma é apagada; contratos antigos seguem intactos porque
     `RentalItem` guarda snapshot congelado da peça. Reativar é um clique na tela
     de produtos.
   - Com a decisão dela: `--pair PREFIXOCODIGO` funde, `--keep PK` mantém a peça
     escolhida e anula as outras, `--quarantine` resolve os casos em que só um
     cadastro tem locação.
   - A migration `catalog.0010` **não aplica** enquanto sobrar código com dois
     itens ativos: ela levanta `RuntimeError` em português com a amostra dos
     códigos, antes de tocar no schema.
9. **Pós-processamento obrigatório:**
   - `python manage.py post_legacy_import --dry-run` e revisar as quantidades;
   - `python manage.py post_legacy_import --apply` para normalizar cidades, reconstruir
     todos os campos de busca de clientes/produtos e zerar preços positivos do cadastro;
   - repetir `python manage.py post_legacy_import --dry-run` e exigir zero pendências;
   - restaurar somente os campos configuráveis da `Company` salvos no passo 5. Para a
     numeração use `max(last_rental_number_salvo, last_rental_number_importado)`; nunca
     sobrescreva PK ou timestamps. O pós-processamento não altera a empresa.
10. **Validar:**
   - `python manage.py homologation_report`
   - `python manage.py cpf_duplicate_report` (esperado: ~518 duplicatas legadas reais;
     não é bug, ver auditoria de 2026-07-20)
   - comparar as contagens centrais com o manifest: clientes, produtos, locações,
     itens e recebíveis;
   - `python manage.py check` e `curl -fsS https://noivaseciabandeirantes.com.br/healthz/`;
   - Conferir na UI uma locação recente conhecida, com itens, recebíveis e valores.
11. **A loja passa a usar só o sistema novo.** O BRcom vira arquivo morto — **não
    desinstale**, guarde o `.mdb` e o executável indefinidamente. Reconcilie o stack com
    `cd ~/noivaecia && ./scripts/deploy.sh --skip-build`, espere app e scheduler
    convergirem e só então valide o health público e reabra a operação.

---

## 3. Se o cliente não puder parar agora

O modelo é **paralelo read-only**, nunca delta:

- Sistema novo recebe import completo e serve para **consulta e treinamento apenas**.
- A loja continua lançando no BRcom por uma ou duas semanas, aprendendo o novo em
  paralelo.
- No cutover real, o que estiver no sistema novo é **descartado** e faz-se **outro import
  completo**. Sem hesitação: é `--reset`, custa segundos.

O único dado perdido nesse modelo é o que alguém digitou durante o treino — que é dado de
treino, descartável por definição. Deixe isso explícito para o cliente antes, para
ninguém achar que lançou contrato de verdade.

---

## 4. Armadilhas conhecidas

| Armadilha | Consequência | Como evitar |
|---|---|---|
| Dois sistemas aceitando escrita | Duas fontes de verdade, reconciliação impossível | Parada rígida do legado antes do export |
| Export de `.mdb` desatualizado | Perde semanas de movimento silenciosamente | Conferir data no manifest do export |
| Pular o `--dry-run` | Descobre problema com as tabelas já apagadas | Sempre dry-run antes, comparando contagens |
| Encerrar após o import bruto | Cidades e campos de busca voltam ao formato legado e correções operacionais se perdem | Executar `post_legacy_import` com dry-run antes e depois; zero pendências é gate de liberação |
| Sem backup antes do `--reset` | Não há ponto de retorno | `scripts/backup.sh` com dump fora da VPS |
| Locação importada como devolvida sem `Return` | Data efetiva inválida no legado deixa o estado inconsistente | Achado documentado na auditoria de 2026-07-29; decidir entre quarentena, rebaixar estado ou sintetizar data |
| Desinstalar o BRcom após o cutover | Perde a única fonte para auditar divergência futura | Arquivar `.mdb` + binários indefinidamente |
| **`--reset` desfaz correções manuais na `Company`** | O nome da empresa volta ao lixo do legado (`empresa.nome` = `'li ap feNOIVAS & CIA'`) e sai impresso no cabeçalho e no rodapé de todo contrato | Aconteceu na carga de 2026-08-02. Salvar antes somente os campos configuráveis e restaurá-los por whitelist; nunca PK/timestamps. Manter `last_rental_number=max(salvo, importado)` |
| **`--reset` repõe preço em produtos** | O preço do traje é digitado no ato da locação e não deve ficar no cadastro; valores do Access passam a pré-preencher o campo na grade | `post_legacy_import` zera todo `Product.value > 0` de forma idempotente e o dry-run final comprova zero pendências |

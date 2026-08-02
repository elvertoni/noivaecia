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

1. **Janela de movimento zero** — loja fechada. Domingo, ou segunda antes de abrir.
   Nunca no meio de um dia de atendimento.
2. **Backup do Postgres de produção antes de qualquer coisa** (`scripts/backup.sh`),
   com o dump guardado **fora da VPS**. Este é o ponto de retorno.
3. **A loja para de usar o BRcom. Parada rígida, anunciada com antecedência.**
   A partir daqui o legado é somente leitura.
4. **Export fresco do `.mdb`** — `tools/legacy_migration/export_access.ps1`
   (PowerShell 32-bit, Jet 4.0). Confirme no manifest que o `.mdb` é o do dia, não uma
   cópia velha.
5. **`import_legacy_access --dry-run`** primeiro. Compare as contagens com o resumo do
   import anterior — clientes, produtos, locações, recebíveis, movimentos financeiros.
   Divergência grande para baixo significa export truncado: pare e investigue.
6. **`import_legacy_access --reset --confirm-reset`** para valer.
7. **Validar:**
   - `python manage.py homologation_report`
   - `python manage.py cpf_duplicate_report` (esperado: ~518 duplicatas legadas reais;
     não é bug, ver auditoria de 2026-07-20)
   - Conferir na UI uma locação recente conhecida, com itens, recebíveis e valores.
8. **A loja passa a usar só o sistema novo.** O BRcom vira arquivo morto — **não
   desinstale**, guarde o `.mdb` e o executável indefinidamente.

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
| Sem backup antes do `--reset` | Não há ponto de retorno | `scripts/backup.sh` com dump fora da VPS |
| Locação importada como devolvida sem `Return` | Data efetiva inválida no legado deixa o estado inconsistente | Achado documentado na auditoria de 2026-07-29; decidir entre quarentena, rebaixar estado ou sintetizar data |
| Desinstalar o BRcom após o cutover | Perde a única fonte para auditar divergência futura | Arquivar `.mdb` + binários indefinidamente |
| **`--reset` desfaz correções manuais na `Company`** | O nome da empresa volta ao lixo do legado (`empresa.nome` = `'li ap feNOIVAS & CIA'`) e sai impresso no cabeçalho e no rodapé de todo contrato | Aconteceu na carga de 2026-08-02. `Company` está em `BUSINESS_MODELS`, então o `--reset` apaga a linha inteira e o import recria a partir do Access. **Antes de qualquer carga**, salvar o registro atual (`Company.objects.values().first()`) e reconferir campo a campo depois; na carga de 02/08 só `name` divergiu |
| **`--reset` repõe preço em 3 produtos** | O preço do traje é digitado no ato da locação e não deve ficar no cadastro, mas 3 dos 10.315 produtos vêm do Access com valor (`CRN83` e `CRN94` = 330,00; `VF1049` = 280,00) e passam a pré-preencher o campo na grade | Zerado manualmente em 2026-08-02. Repetir depois de cada carga: `Product.objects.filter(value__gt=0).update(value=0)` |

# Políticas de Saneamento e Mapeamento Alvo — Sprint R2

> Sprint: R2 — Políticas de saneamento e mapeamento alvo.
> Data-base: 12/06/2026.
> Fonte: análise do snapshot `brcom/`, diagnósticos R1 e código implementado nas sprints R3–R5.
> Escopo: formalizar as nove decisões de política que guiam a importação, o modelo de dados e o comportamento operacional.

---

## R2.01 — Política de datas inválidas

**Status: aprovada**

### Faixa operacional

| Limite | Valor |
|---|---|
| Mínimo aceitável | `1900-01-01` |
| Máximo aceitável | `2035-12-31` |

### Regras por domínio

| Domínio | Regra |
|---|---|
| `locado` — data de retirada/devolução | Grupo de locação inteiro descartado se não houver data mínima válida de retirada. Contado em `suspicious_rental_count` no audit. |
| `locado.dev_efetiva` | `NULL` aceito (533 registros no legado). Coluna deixada nula em `Return.return_date`; nenhum preenchimento automático. |
| `pagar` — vencimento | Data suspeita (`< 1900` ou `> 2035`) não bloqueia importação do título, mas o valor original é preservado na tabela raw `legacy_pagar`. Títulos com vencimento suspeito ficam operacionais com a data original; cabe revisão manual. |
| `movimento` — data | Mesma regra de `pagar`: preserva em raw, importa normalmente. |

### Preservação

- Tabelas `legacy_*` retêm todos os valores originais para auditoria.
- O importador **nunca** converte silenciosamente uma data suspeita em data operacional válida (ex.: substituir por hoje ou por `None` sem registrar).

### Evidência no código

`core/management/commands/import_legacy_access.py`:
- `SUSPICIOUS_DATE_MIN = date(1900, 1, 1)`
- `SUSPICIOUS_DATE_MAX = date(2035, 12, 31)`
- `_safe_date()`: retorna `None` se fora da faixa.
- `_load_rentals()`: pula grupo se `pickup_date is None`.

---

## R2.02 — Política para títulos `pagar` sem locação em `locado`

**Status: aprovada**

### Contexto

26.854 títulos em `pagar` apontam para números de locação que não existem em `locado`. O importador cria 16.864 locações financeiras (stubs) para satisfazer a FK.

### Decisão

**Locação financeira stub** — criar `Rental` sem `RentalItem`, `Pickup` ou `Return`.

Justificativa:
- Preserva o vínculo `Receivable → Rental → Customer` sem perder rastreabilidade.
- Evita títulos soltos sem contexto.
- Locações stub ficam identificáveis: `Rental.items.count() == 0` e foram criadas apenas por `pagar`.

### Comportamento operacional

| Situação | Comportamento |
|---|---|
| Locação stub na lista de locações | Aparece normalmente; sem ações de retirada/devolução (nenhum item). |
| Títulos vinculados a stub | Operacionais como qualquer outro título. |
| Filtro de "locações sem itens" | Relatório de qualidade (R12.09) exibe contagem; revisão manual pode associar manualmente a locação real se encontrada. |

### Evidência no código

`_load_rentals()`: loop separado `pagar_only` cria `Rental` para números sem `locado` correspondente.

---

## R2.03 — Política para `pago=0` com `valor_pago=0`

**Status: aprovada**

### Semântica legada

| Campo legado | Significado |
|---|---|
| `pago = 1` | Título **em aberto** / em carteira |
| `pago = 0` | Título **quitado** / encerrado |

O executável do BRcom grava `pago = '0'` quando `valor = valor_pago`. No entanto, 29.841 registros têm `pago=0` e `valor_pago` pode estar zerado (inconsistência de dados históricos).

### Decisão

Quando `pago = 0` (quitado), forçar `paid_amount = amount` independentemente do `valor_pago` legado.

Justificativa:
- Preserva a semântica operacional: título quitado não deve reaparecer em aberto.
- Divergência original fica auditável em `legacy_pagar.valor_pago`.
- O campo `Receivable.legacy_notes` registra o `valor_pago` original para rastreabilidade.

### Exibição financeira

| Status | `paid_amount` | `balance` | Exibição |
|---|---|---|---|
| `pago=0` (quitado) | `= amount` | `0` | Quitado |
| `pago=1`, `valor_pago > 0` | `= valor_pago` | `amount - valor_pago` | Parcialmente pago |
| `pago=1`, `valor_pago = 0` | `0` | `= amount` | Em aberto |

### Evidência no código

`_load_receivables()`: branch `if row['pago'] == 0: paid_amount = amount`.
`legacy_notes` grava `f"pago={row['pago']} valor_pago={row['valor_pago']}"`.

---

## R2.04 — Política de placeholders

**Status: aprovada**

### Modos de operação

| Flag | Comportamento |
|---|---|
| `--allow-placeholders` | Cria placeholders com `is_placeholder=True`; relatorio de auditoria lista todos criados. |
| `--no-placeholders` | Falha antes de criar qualquer placeholder; lista todos os pendentes. |
| (sem flag) | Falha com instrução para escolher um dos dois modos explicitamente. |

### Domínios com placeholder

| Modelo | Condição | Contagem conhecida |
|---|---|---|
| `Customer` | Referenciado em `locado` ou `pagar` mas ausente em `clientes` | 1 |
| `Category` | Prefixo sem entrada em `categoria` | 16 |
| `Product` | Item em `locado` apontando para produto inexistente | 13 |

### Identificação e saneamento

- Todos os placeholders têm `is_placeholder=True`.
- Filtráveis na manutenção: `Customer.objects.filter(is_placeholder=True)` etc.
- O relatório pós-importação lista IDs e origens.
- O administrador deve corrigir placeholders antes do go-live; nenhum placeholder deve permanecer sem revisão.

### Evidência no código

`_load_customers()`, `_load_categories()`, `_load_products()`: criação com `is_placeholder=True`.
`_write_audit_rows()`: grava contagens de placeholders.

---

## R2.05 — Política de produtos duplicados

**Status: aprovada**

### Contexto

42 pares `(prefixo, codigo)` duplicados em `produtos`. 52 linhas de `locado` apontam para produtos inexistentes (→ 13 produtos placeholder).

### Decisão

**Preservar duplicidade** no Django durante a importação. Não mesclar automaticamente.

Justificativa:
- Mesclagem automática pode associar itens de locação ao produto errado.
- Duplicidade precisa de revisão humana por produto.
- A Sprint R8 implementa desambiguação assistida (RF-CT-01, RF-CT-02).

### Regras

| Regra | Detalhe |
|---|---|
| Importação | Preserva todos os produtos; deduplication apenas dentro do mesmo `locado` (mesmo par `(prefixo, codigo)` na mesma locação usa o primeiro encontrado). |
| Disponibilidade (`AvailabilityView`) | Uso de `.first()` é **limitação conhecida** até R8.03. Consulta com duplicata não deve ser silenciada. |
| R8.03 | Implementar tela de desambiguação quando houver mais de um produto para o mesmo `(prefixo, codigo)`. |
| Identificação | `Product.objects.filter(category=cat, code=code).count() > 1` detecta duplicatas. Badge "Duplicado" em R8.02. |

---

## R2.06 — Política para movimentos sem cliente ou título

**Status: aprovada**

### Contexto

| Situação | Quantidade |
|---|---|
| `movimento` com `cliente IS NULL` | 1.516 |
| `movimento` com `cliente` não-nulo mas órfão (sem `clientes` correspondente) | 4 |
| `movimento` sem `partida` (sem título vinculado) | 22.885 |
| `movimento` com `partida` vinculada a `pagar` existente | 11.006 |

### Decisão

Todos os movimentos de `movimento` são importados como `FinancialMovement` com `source = IMPORT`.

| Situação | Tratamento |
|---|---|
| `movimento` com cliente válido | `FinancialMovement.customer` preenchido. |
| `movimento` com `cliente IS NULL` | `FinancialMovement.customer = None` (movimento manual/importado). |
| `movimento` com cliente órfão | `FinancialMovement.customer = None`; `legacy_notes` registra o ID legado original. |
| `movimento` com `partida` → `pagar` existente | `FinancialMovement.receivable` preenchido. |
| `movimento` com `partida` sem `pagar` correspondente | `FinancialMovement.receivable = None`; `legacy_notes` registra o ID de partida original. |

### Visibilidade

- Movimentos sem cliente visíveis na tela de caixa (R6.01) com filtro "sem cliente".
- Relatório de qualidade (R12.09) conta movimentos sem cliente e sem título.
- Tabela `legacy_movimento` preserva tudo para auditoria.

### Evidência no código

`_load_financial_movements()`: lida com `cliente IS NULL` e `partida` sem correspondente.

---

## R2.07 — Política de multa e juros

**Status: aprovada parcialmente — implementação completa em R6.08**

### Conceitos separados

| Conceito | Origem | Status |
|---|---|---|
| **Juros financeiro** | Vencimento do título em `pagar`; taxa diária em `Company.daily_interest_rate`. | Implementado em `billing.services.compute_interest`. |
| **Multa de atraso na devolução** | `locado.multa`, dias de atraso × valor; calculado por item/locação. | Implementado em `movements.services.compute_penalty`. |
| **Multa moratória financeira** | 2% sobre valor total (cláusula do contrato Crystal). | **Pendente** — delineada mas não separada do juros. |
| **Penalidades contratuais** | 50%/100% por dano, perda, não devolução, desistência ou troca. | **Pendente** — delineada no PRD. |

### Decisões aprovadas

1. Os quatro conceitos **não devem ser colapsados** em uma única taxa genérica.
2. A configuração financeira (`Company` ou nova `FinancialSettings`) deve separar cada tipo.
3. O serviço `billing.services` deve centralizar os cálculos (evitar duplicação nas views).
4. `locado.multa` no legado precisa de interpretação com o usuário-chave antes de ser mapeado ao conceito correto (R0.06 pendente).

### Pendência

- Resposta do usuário-chave sobre `locado.multa` (diária? total? jurídico?).
- Implementação da separação em R6.08–R6.09.

---

## R2.08 — Política de exclusão versus cancelamento

**Status: aprovada**

### Princípio

**Cancelamento é preferido sobre exclusão física.**

Justificativa:
- Preserva histórico de locações, títulos e pagamentos.
- Permite auditoria e reconstituição de eventos.
- Align com o legado: BRcom tinha exclusão em cascata; o sistema novo moderniza para histórico imutável.

### Regras por entidade

| Entidade | Regra |
|---|---|
| `Rental` — cancelar | Permitido; exige motivo (`cancellation_reason`), registra data (`cancelled_at`) e usuário (`cancelled_by`). Disponibilidade liberada. Títulos existentes precisam de decisão financeira (manter, estornar ou quitar). |
| `Rental` — excluir fisicamente | Apenas se: sem `Pickup`, sem `Return`, sem `Payment` vinculado. Exige permissão `rentals.delete`. Registra auditoria. |
| `Customer` — excluir | Bloqueado se houver `Rental` ou `Receivable` vinculado. Inativação disponível como alternativa. |
| `Receivable` — excluir | Apenas com permissão `billing.delete_receivable`. Registra auditoria. Estorno preferido sobre exclusão. |
| `Payment` — excluir | **Proibido.** Estorno cria pagamento negativo; pagamento original fica imutável. |

### Evidência no código

`Rental.Status.CANCELLED` em `rentals/models.py`.
Campos `cancellation_reason`, `cancelled_at`, `cancelled_by` em `Rental`.
`AuditLog` em `core/models.py`.

---

## R2.09 — Política para texto e versão do contrato

**Status: aprovada parcialmente — texto pendente de aprovação do usuário-chave**

### Cláusulas identificadas no legado (Crystal `locacao*.rpt`)

1. Devolução até a data contratada.
2. Multa moratória de 2% sobre valor total.
3. Juros de mora de 1% ao mês e correção monetária pelo INPC/IBGE.
4. Penalidade por danos: 50% do valor do item avariado.
5. Penalidade por perda ou não devolução em até sete dias: 100% do valor do item.
6. Penalidade por desistência: crédito ou cobrança conforme texto do relatorio atual.
7. Penalidade por troca.
8. Campo de assinatura do locador e do locatário.
9. Duas vias (campo `via` na tabela `temp`).

### Decisões aprovadas

| Decisão | Detalhe |
|---|---|
| Cláusulas como template versionado | Texto do contrato deve ser editável e versionado, não hardcoded. |
| Versão no registro da locação | `Rental` preserva referência à versão do contrato impresso (campo a ser adicionado em R7.07–R7.08). |
| Impressão HTML print-friendly | Contrato gerado como página HTML separada com CSS de impressão; duas vias na mesma página. |
| Nenhum PDF por biblioteca de terceiro | Usar `@media print` CSS e orientação de página A4; sem Weasyprint, reportlab ou similar por ora. |

### Pendências

- Aprovação das cláusulas revisadas pelo responsável administrativo/legal (usuário-chave).
- Definição de se a taxa de 1% ao mês (`Company.daily_interest_rate`) alinha com a cláusula contratual ou se precisa de campo separado.
- Implementação em R7.07.

---

## Resumo de status

| Tarefa | Status | Observação |
|---|---|---|
| R2.01 — datas inválidas | Aprovada | Implementada em R4. |
| R2.02 — `pagar` sem `locado` | Aprovada | Locações financeiras stub implementadas em R4. |
| R2.03 — `pago=0` com `valor_pago=0` | Aprovada | Implementada em R4. |
| R2.04 — placeholders | Aprovada | Flags `--allow-placeholders`/`--no-placeholders` em R4. |
| R2.05 — produtos duplicados | Aprovada | Preservação implementada em R4; desambiguação em R8. |
| R2.06 — movimentos sem cliente/título | Aprovada | Importados como `source=IMPORT` em R4. |
| R2.07 — multa e juros | Aprovada parcialmente | Base em R3/R5; separação completa em R6.08. |
| R2.08 — exclusão vs cancelamento | Aprovada | Status `cancelled` e campos de auditoria em R3. |
| R2.09 — texto do contrato | Aprovada parcialmente | Estrutura definida; texto pendente de usuário-chave. |

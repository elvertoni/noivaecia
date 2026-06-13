# TASKS - Refatoracao e Migracao do Legado BRcom

> Fonte principal: `PRD_REFATORACAO.md`.
> Complementos: `PRD.md`, `docs/roadmap.md`, analise do snapshot legado `brcom/`.
> Objetivo: transformar o PRD de refatoracao em sprints executaveis por agentes.

## Como usar

- Marque uma tarefa concluida trocando `[ ]` por `[x]`.
- Preserve os IDs das tarefas; eles sao referencias para dependencias, PRs e testes.
- Quando uma tarefa gerar alteracao visivel, anexar screenshot ou descricao objetiva no PR.
- Quando uma tarefa alterar dados ou migracao, anexar comando executado e resumo de reconciliacao.
- Nenhum agente deve versionar `brcom/`, `.mdb`, `.rpt`, `.exe`, `.dll`, `.ocx`, `.ldb`, `.env`, `db.sqlite3`, `staticfiles/`, `var/` ou dados sensiveis.

## Agentes sugeridos

| Agente | Responsabilidade |
|---|---|
| `migracao` | Exportacao Access, importador, saneamento, auditoria e virada. |
| `financeiro` | Recebiveis, pagamentos, caixa, juros, multas e reconciliacao. |
| `operacao` | Locacoes, contrato, retirada, devolucao e fluxo de balcao. |
| `catalogo` | Produtos, categorias, disponibilidade, duplicados e placeholders. |
| `clientes` | Historico do cliente, busca e protecao de exclusao. |
| `relatorios` | Relatorios operacionais/financeiros, impressao e CSV. |
| `seguranca` | Permissoes granulares, auditoria e controle de acoes sensiveis. |
| `qa` | Testes, performance, homologacao e roteiro de aceite. |
| `ux` | Navegacao, templates, estados vazios, filtros e design system. |

---

## Sprint R0 - Governanca, seguranca e base de trabalho

- [ ] **R0.01** Confirmar o `.mdb` oficial do BRcom para migracao. Agente: `migracao`. Dep: nenhuma. Aceite: caminho, data/hora, tamanho, responsavel e regra de congelamento documentados.
- [x] **R0.02** Registrar que `brcom/` e artefatos legados ficam fora do Git. Agente: `migracao`. Dep: R0.01. Aceite: `git check-ignore -v brcom brcom\brcom.mdb` confirma a regra.
- [ ] **R0.03** Definir janela de congelamento do legado. Agente: `migracao`. Dep: R0.01. Aceite: documento informa quando o legado vira somente leitura e quem autoriza excecoes.
- [x] **R0.04** Definir ambiente oficial de exportacao Access 97/Jet 3.5. Agente: `migracao`. Dep: R0.01. Aceite: Windows PowerShell 32-bit, driver e caminho do banco validados.
- [x] **R0.05** Criar checklist de seguranca pre-commit para a migracao. Agente: `seguranca`. Dep: R0.02. Aceite: checklist cobre dados sensiveis, banco local, dumps, prints e arquivos binarios.
- [ ] **R0.06** Validar perguntas em aberto do `PRD_REFATORACAO.md` com usuario-chave. Agente: `qa`. Dep: R0.01. Aceite: respostas registradas para contas a pagar, multa, contrato, limite de itens e titulos sem locacao.
- [x] **R0.07** Criar mapa de decisoes da migracao. Agente: `migracao`. Dep: R0.06. Aceite: cada decisao tem status `pendente`, `aprovada` ou `rejeitada`.
- [x] **R0.08** Definir criterio de bloqueador P0 para go-live. Agente: `qa`. Dep: R0.06. Aceite: lista objetiva do que impede virada operacional.

## Sprint R1 - Exportacao e diagnostico do legado

- [x] **R1.01** Fortalecer `tools/legacy_migration/export_access.ps1`. Agente: `migracao`. Dep: R0.04. Aceite: valida existencia do `.mdb`, abre conexao antes de criar saida e remove path pessoal default.
- [x] **R1.02** Incluir `exporter_version` no manifest da exportacao. Agente: `migracao`. Dep: R1.01. Aceite: `manifest.json` grava versao do script.
- [x] **R1.03** Incluir SHA-256 do `.mdb` no manifest. Agente: `migracao`. Dep: R1.01. Aceite: manifest permite provar a origem exata da carga.
- [x] **R1.04** Incluir contagens e schema por tabela no manifest. Agente: `migracao`. Dep: R1.01. Aceite: manifest lista tabelas exportadas, colunas e totais.
- [x] **R1.05** Rodar exportacao oficial para `var/legacy_export`. Agente: `migracao`. Dep: R1.01-R1.04. Aceite: `clientes`, `categoria`, `produtos`, `locado`, `pagar`, `movimento`, `empresa`, `programas`, `libera`, `usuario` exportados.
- [x] **R1.06** Criar comando/script de diagnostico somente leitura. Agente: `migracao`. Dep: R1.05. Aceite: gera Markdown e JSON sem alterar o banco Django.
- [x] **R1.07** Diagnosticar contagens e tipos por tabela Access. Agente: `migracao`. Dep: R1.06. Aceite: relatorio lista tabelas, colunas, tipos e total de linhas.
- [x] **R1.08** Diagnosticar chaves orfas. Agente: `migracao`. Dep: R1.06. Aceite: relatorio lista clientes, produtos, locacoes, titulos e movimentos orfaos.
- [x] **R1.09** Diagnosticar produtos duplicados por `(prefixo, codigo)`. Agente: `catalogo`. Dep: R1.06. Aceite: relatorio lista grupos duplicados e total esperado de 42 grupos ou divergencia justificada.
- [x] **R1.10** Diagnosticar prefixos sem categoria. Agente: `catalogo`. Dep: R1.06. Aceite: relatorio separa prefixos vindos de `produtos` e prefixos vindos de `locado`.
- [x] **R1.11** Diagnosticar datas suspeitas. Agente: `migracao`. Dep: R1.06. Aceite: relatorio sinaliza datas antes de 1900, depois de 2035 e `dev_efetiva` nula.
- [x] **R1.12** Diagnosticar financeiro legado. Agente: `financeiro`. Dep: R1.06. Aceite: relatorio confirma `pagar.pago=1` aberto, `pagar.pago=0` encerrado, totais de valor/pago/saldo e vinculos com `movimento.partida`.
- [x] **R1.13** Diagnosticar relatorios Crystal essenciais. Agente: `relatorios`. Dep: R1.05. Aceite: contrato, contas a receber, locados e vendas mapeados para relatorios alvo.

## Sprint R2 - Politicas de saneamento e mapeamento alvo

- [x] **R2.01** Aprovar politica de datas invalidas. Agente: `migracao`. Dep: R1.11. Aceite: regra define bloqueio, preservacao raw e data operacional por dominio.
- [x] **R2.02** Aprovar politica para titulos `pagar` sem locacao em `locado`. Agente: `financeiro`. Dep: R1.12. Aceite: decisao documenta se viram locacao financeira, saldo avulso ou historico arquivado.
- [x] **R2.03** Aprovar politica para `pago=0` com `valor_pago=0`. Agente: `financeiro`. Dep: R1.12. Aceite: regra define como exibir pago/saldo sem perder divergencia auditavel.
- [x] **R2.04** Aprovar politica de placeholders. Agente: `migracao`. Dep: R1.08, R1.10. Aceite: modo estrito falha e modo permissivo cria placeholder com flag/tag e relatorio.
- [x] **R2.05** Aprovar politica de produtos duplicados. Agente: `catalogo`. Dep: R1.09. Aceite: duplicidade preservada, agrupada e tratada na disponibilidade.
- [x] **R2.06** Aprovar politica para movimentos sem cliente/titulo. Agente: `financeiro`. Dep: R1.12. Aceite: movimentos entram como importados/manuais auditaveis ou ficam raw com justificativa.
- [x] **R2.07** Aprovar politica de multa e juros. Agente: `financeiro`. Dep: R0.06. Aceite: separa multa de devolucao, juros financeiro, multa moratoria e penalidades contratuais.
- [x] **R2.08** Aprovar politica de exclusao vs cancelamento. Agente: `operacao`. Dep: R0.06. Aceite: exclusao fisica fica restrita e cancelamento vira regra para historico.
- [x] **R2.09** Aprovar texto e versao inicial do contrato. Agente: `operacao`. Dep: R0.06, R1.13. Aceite: clausulas revisadas e versionadas antes da implementacao.

## Sprint R3 - Modelo de dados e migracoes Django

- [x] **R3.01** Adicionar metadados de origem legada onde necessario. Agente: `migracao`. Dep: R2.01-R2.06. Aceite: modelos alvo suportam `legacy_id`, `legacy_source`, `legacy_notes` ou equivalente.
- [x] **R3.02** Adicionar flags de placeholder. Agente: `migracao`. Dep: R2.04. Aceite: cliente, categoria e produto placeholder podem ser filtrados em manutencao.
- [x] **R3.03** Criar model `Payment`. Agente: `financeiro`. Dep: R2.03. Aceite: pagamento registra recebivel, locacao, cliente, data, valor, juros, desconto, usuario e origem.
- [x] **R3.04** Criar model `CashAccount`. Agente: `financeiro`. Dep: R2.06. Aceite: conta caixa padrao criada por migracao ou comando seguro.
- [x] **R3.05** Criar model `FinancialMovement`. Agente: `financeiro`. Dep: R3.03, R3.04. Aceite: movimento registra data, conta, direcao, valor, historico, origem, cliente opcional, recebivel opcional e legado.
- [x] **R3.06** Ajustar `Receivable` para saldo derivado de pagamentos. Agente: `financeiro`. Dep: R3.03. Aceite: saldo e valor pago ficam recalculaveis sem perder compatibilidade com dados importados.
- [x] **R3.07** Adicionar status `cancelled` em `Rental`. Agente: `operacao`. Dep: R2.08. Aceite: status existe no model, forms e badges.
- [x] **R3.08** Adicionar campo de uso/evento em `Rental`. Agente: `operacao`. Dep: R2.09. Aceite: campo aparece como "Usar em" ou "Uso/evento" e preserva `locado.usar`.
- [x] **R3.09** Adicionar campos de cancelamento em `Rental`. Agente: `operacao`. Dep: R3.07. Aceite: motivo, data e usuario do cancelamento ficam auditaveis.
- [x] **R3.10** Criar model de auditoria de acoes sensiveis. Agente: `seguranca`. Dep: R2.08. Aceite: log registra usuario, acao, objeto, data, motivo e metadados.
- [x] **R3.11** Criar permissoes de acao alem de modulo. Agente: `seguranca`. Dep: R3.10. Aceite: matriz suporta delete, receive, cash, cancel e export.
- [x] **R3.12** Criar migracoes e testes basicos de models. Agente: `qa`. Dep: R3.01-R3.11. Aceite: `python manage.py test` passa nos testes de model adicionados.

## Sprint R4 - Importador idempotente, dry-run e dados brutos

- [x] **R4.01** Tornar importacao raw idempotente. Agente: `migracao`. Dep: R3.01. Aceite: rerun nao duplica tabelas `legacy_*`.
- [x] **R4.02** Garantir preservacao raw das tabelas principais. Agente: `migracao`. Dep: R4.01. Aceite: `legacy_clientes`, `legacy_produtos`, `legacy_categoria`, `legacy_locado`, `legacy_pagar`, `legacy_movimento`, `legacy_programas`, `legacy_libera` ficam consultaveis.
- [x] **R4.03** Implementar `--dry-run` no importador. Agente: `migracao`. Dep: R4.01. Aceite: valida toda a carga sem commit.
- [x] **R4.04** Endurecer `--reset` com backup e confirmacao operacional. Agente: `migracao`. Dep: R4.01. Aceite: reset registra backup antes de qualquer delete.
- [x] **R4.05** Implementar `--no-placeholders`. Agente: `migracao`. Dep: R2.04. Aceite: modo estrito falha com lista de pendencias.
- [x] **R4.06** Implementar `--allow-placeholders`. Agente: `migracao`. Dep: R4.05. Aceite: modo permissivo cria placeholders com flag e relatorio.
- [x] **R4.07** Importar clientes normalizados. Agente: `migracao`. Dep: R4.03-R4.06. Aceite: contagens batem ou divergencias aparecem no relatorio.
- [x] **R4.08** Importar categorias e produtos normalizados. Agente: `catalogo`. Dep: R4.07, R2.05. Aceite: duplicados e placeholders preservados conforme politica.
- [x] **R4.09** Importar locacoes, itens, retiradas e devolucoes. Agente: `migracao`. Dep: R4.08, R3.07-R3.09. Aceite: `locado` reconstruido por numero e datas suspeitas tratadas conforme politica.
- [x] **R4.10** Importar recebiveis com semantica correta de `pagar.pago`. Agente: `financeiro`. Dep: R4.09, R3.03-R3.06. Aceite: `pago=1` vira aberto e `pago=0` vira encerrado conforme politica.
- [x] **R4.11** Importar pagamentos e movimentos financeiros. Agente: `financeiro`. Dep: R4.10, R3.04-R3.05. Aceite: `movimento.partida` vincula a recebivel quando possivel e orfaos ficam auditaveis.
- [x] **R4.12** Registrar auditoria da importacao. Agente: `migracao`. Dep: R4.01-R4.11. Aceite: grava versao importador/exportador, hash `.mdb`, manifest, flags usadas, operador, inicio/fim e resumo.

## Sprint R5 - Financeiro central, recebiveis e baixa

- [x] **R5.01** Criar rotas globais de financeiro. Agente: `financeiro`. Dep: R3.03-R3.06. Aceite: `/financeiro/` ou rota equivalente abre sem depender de locacao.
- [x] **R5.02** Criar dashboard financeiro. Agente: `financeiro`. Dep: R5.01. Aceite: mostra aberto, vencidos, vencendo hoje, proximos 7 dias, recebido hoje, recebido no mes e caixa recente.
- [x] **R5.03** Ajustar sidebar/dashboard para entrada real de Financeiro. Agente: `ux`. Dep: R5.01, R3.11. Aceite: usuario com `billing` ve Financeiro e usuario sem permissao nao ve.
- [x] **R5.04** Criar lista de contas a receber por vencimento. Agente: `financeiro`. Dep: R5.01. Aceite: filtros por periodo, status, cliente, locacao e vencidos.
- [x] **R5.05** Criar lista de contas a receber por cliente. Agente: `financeiro`. Dep: R5.04. Aceite: busca cliente, lista titulos e mostra saldo total.
- [x] **R5.06** Criar baixa parcial e total de titulo. Agente: `financeiro`. Dep: R3.03-R3.06, R5.05. Aceite: baixa cria `Payment`, atualiza saldo e gera `FinancialMovement`.
- [x] **R5.07** Criar baixa multi-titulo por cliente. Agente: `financeiro`. Dep: R5.06. Aceite: usuario seleciona titulos e aplica pagamento distribuido com confirmacao.
- [x] **R5.08** Tratar pagamento maior que saldo. Agente: `financeiro`. Dep: R5.06. Aceite: exige confirmacao e registra credito, troco ou divergencia conforme politica.
- [x] **R5.09** Criar estorno financeiro. Agente: `financeiro`. Dep: R5.06. Aceite: estorno cria movimento reverso, preserva pagamento original e exige motivo.
- [x] **R5.10** Criar testes de baixa e estorno. Agente: `qa`. Dep: R5.06-R5.09. Aceite: testes cobrem parcial, total, excedente e estorno.

## Sprint R6 - Caixa, reconciliacao e financeiro avancado

- [x] **R6.01** Criar tela de movimento de caixa por periodo. Agente: `financeiro`. Dep: R3.04-R3.05. Aceite: filtros por data, conta, direcao, cliente, origem e totalizadores.
- [x] **R6.02** Criar lancamento manual de caixa. Agente: `financeiro`. Dep: R6.01, R3.11. Aceite: exige permissao, direcao explicita, motivo e auditoria.
- [x] **R6.03** Criar relatorio de recebimentos por periodo. Agente: `relatorios`. Dep: R3.03, R5.06. Aceite: lista pagamentos por data, cliente, locacao e totalizadores.
- [x] **R6.04** Criar relatorio de movimento de caixa por periodo. Agente: `relatorios`. Dep: R6.01. Aceite: mostra entradas, saidas e saldo por conta/origem.
- [x] **R6.05** Criar reconciliacao financeira. Agente: `financeiro`. Dep: R4.10-R4.11, R5.06, R6.01. Aceite: compara recebiveis, pagamentos, saldos e movimentos.
- [x] **R6.06** Exportar divergencias de reconciliacao. Agente: `relatorios`. Dep: R6.05. Aceite: CSV opcional lista divergencias por titulo/movimento.
- [x] **R6.07** Criar rotina controlada de recalculo financeiro. Agente: `financeiro`. Dep: R6.05. Aceite: previa antes de alterar, transacao, permissao de manutencao e auditoria.
- [x] **R6.08** Implementar configuracao de regras financeiras. Agente: `financeiro`. Dep: R2.07. Aceite: juros financeiro, multa moratoria e penalidades ficam separados.
- [x] **R6.09** Revisar `billing.services` para nova regra. Agente: `financeiro`. Dep: R6.08. Aceite: calculos centralizados e testados.

## Sprint R7 - Locacoes, contrato e fluxo de balcao

- [x] **R7.01** Reorganizar fluxo de nova locacao. Agente: `operacao`. Dep: R3.08, R5.06. Aceite: cliente, uso/evento, itens, datas, valores e parcelas sao definidos em um fluxo coerente.
- [x] **R7.02** Adicionar busca rapida de cliente na locacao. Agente: `clientes`. Dep: R7.01. Aceite: busca por nome, CPF, RG, telefone e ID legado.
- [x] **R7.03** Adicionar busca rapida de produto na locacao. Agente: `catalogo`. Dep: R7.01. Aceite: busca por prefixo/codigo, descricao, cor e tamanho.
- [x] **R7.04** Validar disponibilidade automaticamente ao adicionar item. Agente: `catalogo`. Dep: R7.03, R8.03. Aceite: item indisponivel bloqueia ou exige decisao conforme regra.
- [x] **R7.05** Integrar parcelas na criacao da locacao. Agente: `financeiro`. Dep: R5.06, R7.01. Aceite: recebiveis criados na mesma transacao da locacao.
- [x] **R7.06** Permitir pagamento de entrada no fluxo da locacao. Agente: `financeiro`. Dep: R7.05. Aceite: entrada cria `Payment` e `FinancialMovement`.
- [x] **R7.07** Implementar contrato imprimivel. Agente: `operacao`. Dep: R2.09, R7.05. Aceite: HTML print-friendly com empresa, cliente, itens, vencimentos, total, multas/juros, assinaturas e duas vias.
- [x] **R7.08** Registrar versao do contrato usada na locacao. Agente: `operacao`. Dep: R7.07. Aceite: locacao preserva referencia da versao impressa.
- [x] **R7.09** Implementar edicao de locacao com regras. Agente: `operacao`. Dep: R7.05. Aceite: `pending` permite editar dados/itens; com pagamento bloqueia alteracao destrutiva salvo permissao.
- [x] **R7.10** Implementar cancelamento de locacao. Agente: `operacao`. Dep: R3.07-R3.09, R5.09. Aceite: status `cancelled`, motivo obrigatorio, politica financeira aplicada e disponibilidade liberada.
- [x] **R7.11** Implementar exclusao segura de locacao. Agente: `operacao`. Dep: R7.10, R3.11. Aceite: exclusao fisica so sem retirada, devolucao e pagamento; caso contrario orienta cancelamento.
- [x] **R7.12** Atualizar detalhe da locacao com acoes contextuais. Agente: `ux`. Dep: R7.07-R7.11. Aceite: editar, cancelar, imprimir, recebimentos, retirada e devolucao aparecem so quando status/permissao permitem.

## Sprint R8 - Catalogo, disponibilidade e saneamento visual

- [x] **R8.01** Expandir filtros da lista de produtos. Agente: `catalogo`. Dep: R4.08. Aceite: filtros por prefixo, codigo, descricao, cor, tamanho, duplicados e placeholders.
- [x] **R8.02** Criar indicadores de duplicado e placeholder em produto/categoria. Agente: `catalogo`. Dep: R3.02, R8.01. Aceite: listagens mostram badges claros.
- [x] **R8.03** Corrigir disponibilidade para nao usar `.first()` em duplicados. Agente: `catalogo`. Dep: R8.01. Aceite: duplicidade exibe desambiguacao com descricao, cor, tamanho e historico.
- [x] **R8.04** Criar historico por produto. Agente: `catalogo`. Dep: R8.03. Aceite: produto mostra locacoes recentes, status e datas.
- [x] **R8.05** Criar rotina de saneamento de prefixos placeholder. Agente: `catalogo`. Dep: R3.02, R4.08. Aceite: tela lista prefixos placeholder e permite revisar.
- [x] **R8.06** Criar mesclagem transacional de categoria/prefixo. Agente: `catalogo`. Dep: R8.05, R3.10. Aceite: previa, confirmacao, auditoria e atualizacao de produtos/itens relacionados.
- [x] **R8.07** Garantir valor sugerido do produto sem sobrescrever historico. Agente: `catalogo`. Dep: R7.03. Aceite: `Product.value` sugere e `RentalItem.value` preserva valor contratado.
- [x] **R8.08** Criar testes de catalogo e disponibilidade com duplicados. Agente: `qa`. Dep: R8.01-R8.07. Aceite: testes cobrem filtros, duplicidade e placeholders.

## Sprint R9 - Clientes e historico operacional

- [x] **R9.01** Melhorar busca de clientes. Agente: `clientes`. Dep: R4.07. Aceite: busca por nome, CPF, RG, telefones e ID legado.
- [x] **R9.02** Criar detalhe/historico do cliente. Agente: `clientes`. Dep: R5.06, R7.01. Aceite: tela abre pela lista de clientes e mostra dados, locacoes, itens, recebiveis, pagamentos e saldo.
- [x] **R9.03** Adicionar filtros no historico do cliente. Agente: `clientes`. Dep: R9.02. Aceite: filtros por periodo, status da locacao, status financeiro e produto.
- [x] **R9.04** Adicionar resumo financeiro no cliente. Agente: `financeiro`. Dep: R9.02, R5.06. Aceite: total locado, pago, aberto, juros/multas batem com recebiveis e pagamentos.
- [x] **R9.05** Otimizar queries do historico do cliente. Agente: `clientes`. Dep: R9.02-R9.04. Aceite: usa `select_related`/`prefetch_related` e teste de query count evita N+1.
- [x] **R9.06** Bloquear exclusao fisica de cliente com historico. Agente: `clientes`. Dep: R3.11. Aceite: cliente com locacao/recebivel nao pode ser excluido fisicamente.
- [x] **R9.07** Implementar inativacao de cliente. Agente: `clientes`. Dep: R9.06. Aceite: cliente inativo preserva historico e pode ser filtrado.
- [x] **R9.08** Criar testes de busca, historico e inativacao. Agente: `qa`. Dep: R9.01-R9.07. Aceite: testes cobrem campos de busca, permissao, totais e bloqueio de exclusao.

## Sprint R10 - Retirada, devolucao e operacao diaria

- [x] **R10.01** Criar visao por data de produtos a retirar. Agente: `operacao`. Dep: R7.09. Aceite: filtros por periodo, cliente, prefixo/codigo e status nao retirado.
- [x] **R10.02** Criar acao de retirada a partir da visao por data. Agente: `operacao`. Dep: R10.01. Aceite: retirada exige confirmacao e atualiza status corretamente.
- [x] **R10.03** Criar visao por data de produtos retirados. Agente: `operacao`. Dep: R10.02. Aceite: filtros por retirada, previsao de devolucao, cliente e produto.
- [x] **R10.04** Criar visao de atrasados/nao devolvidos. Agente: `operacao`. Dep: R10.03. Aceite: lista locacoes retiradas com retorno vencido e dias de atraso.
- [x] **R10.05** Integrar devolucao com saldo financeiro. Agente: `financeiro`. Dep: R5.06, R10.03. Aceite: devolucao mostra saldo da locacao e permite receber no mesmo fluxo.
- [x] **R10.06** Aplicar politica de multa na devolucao. Agente: `financeiro`. Dep: R2.07, R10.05. Aceite: multa calculada por regra unica e testada.
- [x] **R10.07** Criar rotina de acerto de devolucoes. Agente: `operacao`. Dep: R10.01-R10.06. Aceite: encontra `Rental returned` sem `Return`, `Return` sem status, `Pickup` ausente e datas invalidas.
- [x] **R10.08** Criar testes de retirada, devolucao e atraso. Agente: `qa`. Dep: R10.01-R10.07. Aceite: testes cobrem status, multa, pagamento na devolucao e inconsistencias.

## Sprint R11 - Relatorios, impressao e exportacao

- [x] **R11.01** Refatorar `ReportView` para servicos por tipo. Agente: `relatorios`. Dep: R5-R10. Aceite: cada relatorio tem query/service isolado e template renderizavel.
- [x] **R11.02** Implementar relatorio de produtos a retirar. Agente: `relatorios`. Dep: R10.01. Aceite: equivalente operacional ao `locados.rpt` para nao retirados.
- [x] **R11.03** Implementar relatorio de produtos retirados. Agente: `relatorios`. Dep: R10.03. Aceite: mostra itens retirados por periodo e filtros.
- [x] **R11.04** Implementar relatorio de devolvidos. Agente: `relatorios`. Dep: R10.05. Aceite: filtra por devolucao efetiva, cliente e produto.
- [x] **R11.05** Implementar relatorio de atrasados/nao devolvidos. Agente: `relatorios`. Dep: R10.04. Aceite: mostra dias de atraso e status financeiro resumido.
- [x] **R11.06** Implementar relatorio de vendas/locacoes realizadas. Agente: `relatorios`. Dep: R7.01. Aceite: equivalente funcional ao `vendas.rpt`.
- [x] **R11.07** Implementar relatorio contas a receber por vencimento. Agente: `relatorios`. Dep: R5.04. Aceite: equivalente funcional ao `receber.rpt`.
- [x] **R11.08** Implementar relatorio contas a receber por cliente. Agente: `relatorios`. Dep: R5.05. Aceite: equivalente funcional ao `receberc.rpt`.
- [x] **R11.09** Implementar impressao HTML para relatorios criticos. Agente: `relatorios`. Dep: R11.02-R11.08. Aceite: cabecalho mostra empresa, filtros e data/hora de emissao.
- [x] **R11.10** Implementar exportacao CSV para relatorios criticos. Agente: `relatorios`. Dep: R11.02-R11.08. Aceite: CSV respeita filtros e permissao.
- [x] **R11.11** Criar testes de relatorios. Agente: `qa`. Dep: R11.01-R11.10. Aceite: fixtures pequenas validam filtros, totais, impressao e CSV.

## Sprint R12 - Permissoes granulares, auditoria e manutencao

- [x] **R12.01** Criar matriz editavel de permissoes de acao por usuario. Agente: `seguranca`. Dep: R3.11. Aceite: administrador configura modulos e acoes no mesmo fluxo ou fluxo complementar.
- [x] **R12.02** Aplicar permissoes finas em exclusoes. Agente: `seguranca`. Dep: R12.01. Aceite: UI oculta acao e POST sem permissao retorna 403.
- [x] **R12.03** Aplicar permissoes finas em baixa/estorno financeiro. Agente: `seguranca`. Dep: R12.01, R5.06-R5.09. Aceite: backend protege todas as mutacoes.
- [x] **R12.04** Aplicar permissoes finas em caixa manual. Agente: `seguranca`. Dep: R12.01, R6.02. Aceite: somente usuario autorizado cria movimento manual.
- [x] **R12.05** Aplicar permissoes finas em cancelamento de locacao. Agente: `seguranca`. Dep: R12.01, R7.10. Aceite: cancelar exige permissao especifica.
- [x] **R12.06** Aplicar permissoes finas em exportacao de relatorios. Agente: `seguranca`. Dep: R12.01, R11.10. Aceite: exportar sem permissao retorna 403.
- [x] **R12.07** Importar `programas` e `libera` como referencia auditavel. Agente: `migracao`. Dep: R4.02. Aceite: dados legados visiveis apenas para admin/auditoria e senha legada nao migra.
- [x] **R12.08** Registrar auditoria para acoes sensiveis. Agente: `seguranca`. Dep: R3.10, R12.02-R12.06. Aceite: log cobre exclusao, cancelamento, baixa, estorno, caixa e configuracoes financeiras.
- [x] **R12.09** Criar painel de qualidade da importacao em manutencao. Agente: `migracao`. Dep: R4.12, R6.05, R8.05. Aceite: cards mostram placeholders, duplicados, datas suspeitas, locacoes financeiras sem itens e movimentos orfaos.
- [x] **R12.10** Criar rotinas controladas de manutencao. Agente: `migracao`. Dep: R12.09. Aceite: recalcular totais, saldos, reconciliacao e acertos com previa, transacao e auditoria.
- [x] **R12.11** Criar testes de permissoes e auditoria. Agente: `qa`. Dep: R12.01-R12.10. Aceite: testes validam 200/403, links ocultos e registros de auditoria.

## Sprint R13 - UX, performance e acabamento operacional

- [x] **R13.01** Padronizar filtros em listas principais. Agente: `ux`. Dep: R5-R11. Aceite: filtros aparecem em clientes, produtos, locacoes, financeiro e relatorios.
- [x] **R13.02** Padronizar estados vazios em pt-BR. Agente: `ux`. Dep: R13.01. Aceite: telas sem resultado orientam a proxima acao.
- [x] **R13.03** Padronizar botoes e acoes conforme design system. Agente: `ux`. Dep: R13.01. Aceite: acoes primarias, secundarias e perigo seguem classes existentes.
- [x] **R13.04** Melhorar densidade das tabelas operacionais. Agente: `ux`. Dep: R13.01. Aceite: tabelas continuam legiveis com base grande e sem cards aninhados.
- [x] **R13.05** Revisar navegacao lateral por grupos. Agente: `ux`. Dep: R5.03, R11, R12. Aceite: Financeiro, Movimentacao, Consultas e Administracao ficam coerentes com permissoes.
- [x] **R13.06** Validar responsividade das telas novas. Agente: `ux`. Dep: R13.01-R13.05. Aceite: telas funcionam em desktop, tablet e mobile sem sobreposicao.
- [x] **R13.07** Otimizar queries de listas grandes. Agente: `qa`. Dep: R5-R11. Aceite: usa `select_related`, `prefetch_related`, indices e paginacao onde necessario.
- [x] **R13.08** Adicionar indices para consultas criticas. Agente: `qa`. Dep: R13.07. Aceite: indices cobrem vencimento, status, cliente, locacao, produto/prefixo/codigo e movimentos por data.
- [x] **R13.09** Rodar auditoria visual das telas novas. Agente: `ux`. Dep: R13.01-R13.08. Aceite: screenshots ou notas registram ajustes finais.
- [x] **R13.10** Rodar suite completa de testes. Agente: `qa`. Dep: R13.01-R13.09. Aceite: `python manage.py test` passa.

## Sprint R14 - Homologacao, virada e aceite final

- [x] **R14.01** Reimportar em homologacao limpa. Agente: `migracao`. Dep: R4-R12. Aceite: base limpa recebe raw e normalizado do zero.
- [x] **R14.02** Gerar relatorio pos-importacao de contagens. Agente: `migracao`. Dep: R14.01. Aceite: compara Access exportado vs Django para clientes, produtos, locacoes, itens, recebiveis, pagamentos e movimentos.
- [x] **R14.03** Gerar relatorio de placeholders. Agente: `migracao`. Dep: R14.01. Aceite: lista cliente, categorias e produtos placeholder com IDs e origem.
- [x] **R14.04** Gerar relatorio de datas suspeitas. Agente: `migracao`. Dep: R14.01. Aceite: lista registros bloqueados, ajustados e preservados apenas em raw/auditoria.
- [x] **R14.05** Gerar relatorio de reconciliacao financeira. Agente: `financeiro`. Dep: R14.01. Aceite: compara titulos, pagamentos, saldos e movimentos; lista divergencias.
- [ ] **R14.06** Validar amostras manuais com usuario-chave. Agente: `qa`. Dep: R14.02-R14.05. Aceite: amostras de cliente, produto duplicado, locacao, titulo, baixa e movimento aprovadas.
- [ ] **R14.07** Executar roteiro operacional em homologacao. Agente: `qa`. Dep: R14.06. Aceite: criar locacao, consultar disponibilidade, retirar, devolver, baixar titulo, conferir caixa, imprimir contrato e consultar historico.
- [x] **R14.08** Rodar dry-run final com `.mdb` congelado. Agente: `migracao`. Dep: R14.07. Aceite: relatorio sem bloqueadores P0.
- [x] **R14.09** Executar backup final antes da virada. Agente: `migracao`. Dep: R14.08. Aceite: backup do `.mdb` e do SQLite alvo registrado com hash/data.
- [ ] **R14.10** Executar importacao final em producao. Agente: `migracao`. Dep: R14.09. Aceite: import concluido, audit log gravado e relatorios pos-importacao anexados.
- [ ] **R14.11** Bloquear legado ou deixa-lo somente leitura. Agente: `migracao`. Dep: R14.10. Aceite: data/hora da virada registrada e equipe orientada.
- [ ] **R14.12** Aceite final da migracao. Agente: `qa`. Dep: R14.10-R14.11. Aceite: financeiro reconciliado ou divergencias listadas, placeholders conhecidos, datas suspeitas tratadas e auditoria completa disponivel.

---

## Checklist de aceite global

- [x] **G.01** Financeiro central acessivel sem entrar por locacao.
- [x] **G.02** Contas a receber por vencimento e por cliente funcionais.
- [x] **G.03** Baixas geram historico de pagamento e movimento de caixa.
- [x] **G.04** Contrato de locacao imprimivel com dados, itens, parcelas e clausulas.
- [x] **G.05** Historico do cliente disponivel e performatico.
- [x] **G.06** Disponibilidade trata produtos duplicados sem selecionar `.first()` silenciosamente.
- [x] **G.07** Relatorios operacionais e financeiros equivalentes aos Crystal essenciais.
- [x] **G.08** Importacao gera relatorio antes e depois da carga.
- [x] **G.09** Totais financeiros reconciliados ou divergencias listadas.
- [x] **G.10** Permissoes impedem exclusoes, baixas, caixa manual e exportacoes indevidas.
- [x] **G.11** Testes cobrem financeiro, migracao, disponibilidade, permissoes, relatorios e fluxos criticos.
- [ ] **G.12** Legado congelado ou somente leitura apos a virada.

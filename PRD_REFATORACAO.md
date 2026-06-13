# PRD Refatoracao - Migracao Legado BRcom para Noivas & Cia

> Documento de requisitos de refatoracao e saneamento pos-analise do legado.
> Data-base da analise: 12/06/2026.
> Fonte legado local: `brcom/` (snapshot integral, fora do Git).
> Sistema alvo: Django 5 monolitico, DTL + TailwindCSS, SQLite.

---

## 1. Objetivo

Este documento define os ajustes, otimizacoes e refatoracoes necessarias para que o sistema Django Noivas & Cia substitua com seguranca operacional o app legado BRcom, preservando as funcionalidades realmente usadas, corrigindo lacunas do MVP atual e reduzindo riscos de migracao de dados.

O foco nao e copiar cegamente o sistema antigo. O objetivo e:

- Preservar o conhecimento operacional embutido no legado.
- Corrigir inconsistencias, nomenclaturas confusas e dados corrompidos.
- Completar lacunas funcionais do Django atual, especialmente o financeiro.
- Criar uma virada controlada, auditavel e reversivel.
- Manter o produto simples, web, server-side e aderente aos padroes do projeto.

---

## 2. Resumo executivo

O legado e um sistema Visual Basic 6 com banco Microsoft Access 97/Jet 3.5 e relatorios Crystal Reports. Ele cobre mais do que o MVP Django atual:

- Cadastros de clientes, categorias, produtos, empresa e usuarios.
- Criacao de locacoes com ate 15 itens.
- Impressao de contrato de locacao em duas vias.
- Retirada e devolucao com marcacao por item/locacao.
- Consulta de disponibilidade por produto e data.
- Recebimento de clientes com titulos em carteira, pagamentos parciais e juros.
- Movimento de contas/caixa vinculado a recebimentos.
- Historico do cliente.
- Relatorios operacionais e financeiros por data, cliente e vencimento.
- Permissoes por programa, mais granulares que os modulos atuais.

O Django atual ja cobre a espinha dorsal:

- Clientes.
- Categorias e produtos.
- Empresa singleton.
- Locações com itens.
- Retirada/devolucao.
- Recebiveis por locacao.
- Disponibilidade.
- Relatorios basicos.
- Usuarios e permissao por modulo.
- Importacao bruta e normalizada do Access exportado.

As lacunas mais importantes sao:

1. Financeiro incompleto: falta uma tela financeira central, baixa por cliente, contas a receber por vencimento/cliente, historico de pagamentos, movimento de caixa e reconciliacao.
2. Locacao incompleta: falta editar/cancelar/excluir locacao com regras, imprimir contrato, limitar/validar itens e gerar parcelas no fluxo de criacao.
3. Relatorios insuficientes: falta reproduzir os relatorios Crystal essenciais.
4. Historico do cliente ausente: o legado tem uma tela especifica de historico.
5. Importacao precisa de mais validacao e saneamento: ha datas invalidas, placeholders, chaves duplicadas e titulos financeiros sem itens.
6. Permissoes atuais sao amplas demais: o legado controla programas como exclusao de cliente, exclusao de locacao e exclusao de titulos.

---

## 3. Fontes analisadas

### 3.1 Snapshot legado

Diretorio local: `brcom/`.

Arquivos relevantes encontrados:

| Item | Evidencia | Observacao |
|---|---|---|
| Banco principal | `brcom/brcom.mdb` | Access 97/Jet 3.5, abre via ODBC 32-bit. |
| Banco do instalador | `brcom/Setup/Support/brcom.mdb` | Estrutura inicial menor, mesmo conjunto base de tabelas. |
| Executaveis | `brcom/brcom_old.exe`, `brcom/brcom_noivas - Copia.exe`, `brcom/Setup/Support/brcom_noivas.exe` | VB6; strings revelam menus, telas e SQLs. |
| Relatorios Crystal | `locacao*.rpt`, `locados*.rpt`, `receber*.rpt`, `vendas.rpt` | Contrato, itens a retirar/retirados, contas a receber e vendas. |
| Runtime | `MSVBVM60.DLL`, `Dao350.dll`, `MSJet35.dll`, OCXs | Confirma tecnologia VB6 + DAO/Jet 3.5. |
| Backup manual | `backup.bat` | Copia `C:\BRCOM_NOIVAS\BRCOM.MDB` e `*.RPT`. |

### 3.2 Tabelas Access identificadas

| Tabela | Registros | Funcao inferida |
|---|---:|---|
| `categoria` | 54 | Prefixos/categorias de produtos. |
| `clientes` | 18.783 | Cadastro de clientes. |
| `empresa` | 1 | Configuracao da loja, ultima locacao, juros e rodape. |
| `libera` | 16 | Programas liberados por usuario. |
| `locado` | 71.099 | Itens de locacao; a locacao e agrupada por numero. |
| `movimento` | 33.891 | Movimentos financeiros/caixa. |
| `pagar` | 54.916 | Titulos financeiros de clientes, apesar do nome confuso. |
| `programas` | 16 | Cadastro de permissoes/telas do legado. |
| `produtos` | 10.258 | Cadastro de produtos/acervo. |
| `temp` | 39.050 | Tabela temporaria para impressao de contrato em vias. |
| `usuario` | 1 | Usuario/senha do legado. |

### 3.3 Contagens da base Django local pos-importacao

A base SQLite local ja possui dados importados:

| Modelo Django | Registros |
|---|---:|
| `Customer` | 18.784 |
| `Category` | 70 |
| `Product` | 10.271 |
| `Rental` | 35.654 |
| `RentalItem` | 71.099 |
| `Pickup` | 18.666 |
| `Return` | 18.630 |
| `Receivable` | 54.916 |

Audit log da importacao:

| Indicador | Valor |
|---|---:|
| Clientes placeholder | 1 |
| Categorias placeholder | 16 |
| Produtos placeholder | 13 |
| Chaves de produto duplicadas | 42 |
| Locações vindas de `locado` | 18.790 |
| Locações criadas apenas a partir de `pagar` | 16.864 |

---

## 4. Inventario funcional do legado

### 4.1 Programas/permissoes cadastrados no Access

| Codigo | Descricao legado | Equivalente Django atual | Gap |
|---|---|---|---|
| `bvscad` | Cadastro de Clientes | `customers` | Basico implementado. Falta historico detalhado no cadastro. |
| `bvsexcc` | Exclusao de Clientes | Dentro de `customers` | Permissao atual e ampla; precisa permissao fina. |
| `bvscateg` | Cadastro de Categoria | `catalog` | Implementado. Precisa tratar prefixos placeholder e duplicidades. |
| `bvsprod` | Cadastro de Produtos | `catalog` | Implementado. Falta busca avancada e saneamento de duplicados. |
| `bvsemp` | Cadastro Empresa | `company` | Implementado. Precisa revisar encoding e clausulas contratuais. |
| `bvsretira` | Informa Retirada | `movements` | Implementado por locacao; precisa relatorio/acao em lote por data. |
| `bvsdevolve` | Informa Devolucao | `movements` | Implementado por locacao; precisa baixa financeira integrada. |
| `bvsdisp` | Ver Disponibilidade do Produto | `catalog.availability` | Implementado de forma simples; precisa lidar duplicados e status parcial. |
| `bvsbaixa` | Recebimento de Clientes | `billing` | Parcial. Falta tela central de baixa por cliente/titulo. |
| `bvscaixa` | Movimento de Contas | Nao normalizado | Gap critico. `movimento` nao tem model/tela. |
| `receber` | Contas a Receber por Vencimento | `billing/reports` parcial | Falta relatorio/tela global por vencimento. |
| `receberc` | Contas a Receber por Cliente | `billing/reports` parcial | Falta consulta por cliente. |
| `exctit` | Exclusao de Titulos em Carteira | Nao granular | Falta permissao e fluxo seguro. |
| `vendas` | Listagem das Locacoes Realizadas | `reports` parcial | Falta relatorio financeiro/operacional equivalente. |
| `bvsrel` | Relatorio de Acompanhamento | `reports` parcial | Implementado parcialmente; falta fidelidade aos filtros antigos. |
| `bvsexcl` | Exclusao de Locacoes | Nao implementado | Gap: falta cancelar/excluir locacao com regras. |

### 4.2 Menus e telas extraidos do executavel

Strings relevantes do executavel indicam as seguintes telas/acoes:

- `CADASTRO DE CATEGORIA`.
- `CADASTRO DE PRODUTOS`.
- `Cadastro de Clientes`.
- `CADASTRO EMPRESA`.
- `USUARIOS`.
- `LIBERAR E BLOQUEAR USUARIOS`.
- `ALTERAR SENHA USUARIO`.
- `INFORMA RETIRADA`.
- `INFORMA DEVOLUCAO`.
- `RECEBIMENTO DE CLIENTES`.
- `MOVIMENTACAO DE CONTAS`.
- `ENTRADA DE TITULOS A PAGAR`.
- `CONTAS A RECEBER`.
- `CONTAS A PAGAR`.
- `FORNECEDORES`.
- `POR FORNECEDOR`.
- `DISPONIBILIDADE DO PRODUTO`.
- `HISTORICO DO CLIENTE`.
- `RELATORIOS`.
- `PRODUTOS A RETIRAR/RETIRADOS`.
- `VENDAS REALIZADAS`.
- `EXCLUIR CLIENTE`.
- `EXCLUIR PRODUTO`.
- `EXCLUIR LOCACAO`.
- `ACERTA DEVOLUCOES`.
- `CONVERTER CLIENTES`, `CONVERTER PRODUTOS`, `CONVERTER LOCACOES`.

Observacao: o banco principal nao possui tabelas explicitas de fornecedores, bancos ou contas a pagar separadas. Essas strings podem ser menus residuais, funcionalidades incompletas ou funcionalidades gravadas em tabelas genericas. Precisam de validacao com usuario antes de entrar no escopo de implementacao.

### 4.3 Relatorios Crystal identificados

| Arquivo | Funcao inferida | Campos/condicoes observados |
|---|---|---|
| `locacao*.rpt` | Contrato de locacao | Empresa, cliente, CPF/RG, endereco, telefones, itens, vencimentos `venc1..venc9`, retirada, devolucao, multa, clausulas legais. |
| `locados.rpt`, `locados12.rpt` | Produtos a retirar/retirados | Cliente, locacao, prefixo/codigo, descricao, retirada, retorno, uso. |
| `receber.rpt` | Contas a receber por vencimento | Filtro `{pagar.pago} = 1`; vencimento, cliente, locacao, valor, pago, saldo, ultimo pagamento. |
| `receberc.rpt` | Contas a receber por cliente | Mesmo dominio, agrupado/filtrado por cliente. |
| `vendas.rpt` | Locacoes realizadas | Filtro `{locado.retirado} = 1`; locacao, cliente, retirada, valor. |

---

## 5. Mapa de dados legado

### 5.1 `clientes` -> `customers.Customer`

| Campo legado | Campo atual | Observacao |
|---|---|---|
| `numero` | `Customer.id` na importacao | Preserva chave legada. |
| `nome` | `name` | Obrigatorio no Django; cria placeholder se ausente/referenciado. |
| `endereco` | `address` | No Access o campo e `endereço`. |
| `bairro` | `district` | OK. |
| `cidade` | `city` | OK. |
| `rg` | `rg` | OK. |
| `cpf` | `cpf` | Precisa mascara/validacao opcional. |
| `telefone` | `phone_home` | OK. |
| `celular` | `phone_mobile` | OK. |
| `fone_cial` | `phone_work` | OK. |
| `obs` | `notes` | Legado limita a 60 chars; Django amplia. |

### 5.2 `categoria` + `produtos` -> `catalog`

| Campo legado | Campo atual | Observacao |
|---|---|---|
| `categoria.prefixo` | `Category.prefix` | Unico no Django. |
| `categoria.categoria` | `Category.name` | Algumas descricoes tem lixo/erro de digitacao. |
| `produtos.id` | `Product.id` na importacao | Preserva chave. |
| `produtos.codigo` | `Product.code` | Nao e unico sozinho. |
| `produtos.prefixo` | `Product.category` | Ha prefixos sem categoria. |
| `produtos.descrição` | `Product.description` | OK. |
| `produtos.cor` | `Product.color` | OK. |
| `produtos.tamanho` | `Product.size` | OK. |
| `produtos.valor` | `Product.value` | Soma total baixa indica uso irregular; locacao guarda valor real. |
| `produtos.obs` | `Product.notes` | OK. |

Problemas encontrados:

- 16 prefixos aparecem em produtos sem cadastro correspondente em `categoria`.
- 11 prefixos aparecem em locacoes sem cadastro correspondente em `categoria`.
- 42 pares `(prefixo, codigo)` duplicados em `produtos`.
- 52 linhas de `locado` apontam para produtos inexistentes; importacao atual cria 13 produtos placeholder.

### 5.3 `locado` -> `Rental` + `RentalItem` + `Pickup` + `Return`

O legado nao tem uma tabela cabecalho de locacao. Cada item fica em `locado`, e a locacao e reconstruida agrupando pelo campo `locação`.

| Campo legado | Campo atual | Observacao |
|---|---|---|
| `locação` | `Rental.number` e `Rental.id` na importacao | Chave de agrupamento. |
| `cliente` | `Rental.customer` | Todos os itens `locado` apontam para cliente existente. |
| `retirada` | `Rental.pickup_date` e `Pickup.pickup_date` | Usar minima data do grupo. |
| `dev_prevista` | `Rental.return_date` | Usar maxima data do grupo. |
| `devolvido` | `Rental.status` / `Return` | Todos devolvidos => returned. |
| `retirado` | `Rental.status` / `Pickup` | Algum retirado => picked_up se nao devolvido. |
| `dev_efetiva` | `Return.return_date` | 533 linhas sem data efetiva. |
| `prefixo`, `codigo` | `RentalItem.product` | Mapeamento por categoria+codigo. |
| `valor` | `RentalItem.value` e soma em `Rental.total_value` | OK. |
| `multa` | `Rental.penalty_value` | Legado armazena multa por item/locacao; regra precisa confirmar se e diaria ou total. |
| `usar` | `Rental.notes` ou campo especifico | Importacao atual joga em notes. Deve virar campo `use_for`/`event_notes`. |
| `obs` | `Rental.notes` ou `RentalItem.description/notes` | Precisa preservar por item quando fizer sentido. |

Dados relevantes:

- 71.099 itens.
- 18.790 locacoes distintas em `locado`.
- Ate 15 itens por locacao.
- 395 itens pendentes nao retirados/devolvidos.
- 138 itens retirados e nao devolvidos.
- 70.566 itens devolvidos.
- 27 linhas em `locado` tem datas fora da faixa operacional normal.

### 5.4 `pagar` -> `billing.Receivable`

Apesar do nome `pagar`, o uso principal e contas a receber de clientes.

Semantica critica:

- Relatorios `receber.rpt` e `receberc.rpt` filtram `{pagar.pago} = 1`.
- O executavel baixa titulo com `update pagar set [valor_pago] = [valor_pago] + ...`.
- Quando `valor = valor_pago`, o executavel grava `pago = '0'`.
- Portanto, no legado:
  - `pago = 1` significa titulo em aberto/em carteira.
  - `pago = 0` significa quitado/encerrado.

| Campo legado | Campo atual | Observacao |
|---|---|---|
| `id` | `Receivable.id` na importacao | Preserva chave. |
| `vencimento` | `due_date` | Ha datas invalidas. |
| `valor` | `amount` | OK. |
| `valor_pago` | `paid_amount` | Para `pago=0` antigo pode estar zerado mesmo quitado. |
| `pago` | Derivado para `balance`/status | Nao deve virar booleano com o mesmo nome. |
| `ult_pagto` | `last_payment_date` | 52.779 titulos sem data de ultimo pagamento. |
| `usuario` | Ausente | Deve virar auditoria ou import metadata, se util. |
| `locação` | `rental` | 26.854 titulos apontam para locacao sem itens em `locado`; importacao criou locacoes financeiras. |
| `cliente` | `rental.customer` ou campo redundante auditado | 1 titulo aponta para cliente inexistente. |

Dados relevantes:

- 54.916 titulos.
- 29.841 registros com `pago=0`.
- 25.075 registros com `pago=1`.
- Soma `valor`: 9.184.935,00.
- Soma `valor_pago`: 497.171,00.
- 13 vencimentos antes de 1900.
- 3 vencimentos depois de 2035.
- Na base Django importada: 25.036 recebiveis em aberto e 29.880 quitados.

### 5.5 `movimento` -> novo modelo financeiro

`movimento` ainda nao possui model normalizado no Django.

| Campo legado | Funcao inferida |
|---|---|
| `id` | Identificador do movimento. |
| `data` | Data do movimento. |
| `conta` | Conta/caixa. No banco analisado, todos os movimentos usam conta `1`. |
| `cliente` | Cliente relacionado; 1.516 nulos e 4 orfaos nao nulos. |
| `valor` | Valor movimentado. |
| `historico` | Historico textual. |
| `es` | Entrada/Saida. No banco analisado, todos estao como `E`. |
| `partida` | Link com `pagar.id` em 11.006 movimentos. |

Dados relevantes:

- 33.891 movimentos.
- Soma de entradas: 5.831.742,00.
- Intervalo: 08/12/2006 a 08/06/2026.
- 11.006 movimentos vinculam com titulos `pagar`.

Conclusao: o financeiro atual nao pode ser considerado equivalente ao legado enquanto `movimento` nao for normalizado ou, no minimo, preservado com consulta/auditoria operacional.

### 5.6 `temp`

Tabela temporaria de impressao:

- 39.050 registros.
- Campo `via` com 19.525 registros para via `1` e 19.525 para via `2`.
- Mesma estrutura de `locado`, acrescida de `via`.

Conclusao: nao deve virar tabela de dominio permanente. Serve para entender impressao de contrato em duas vias e deve ser substituida por geracao dinamica de contrato.

---

## 6. Estado atual do Django

### 6.1 Implementado e aproveitavel

- Arquitetura modular por apps.
- Auth por e-mail.
- Permissoes por modulo.
- CRUD de clientes.
- CRUD de categorias e produtos.
- Empresa singleton e sequencial de locacao.
- Criacao de locacao com formset de itens.
- Foto de comprovacao por item.
- Registro de retirada/devolucao.
- Recebiveis com saldo derivado.
- Juros por dia sobre saldo em aberto.
- Disponibilidade por produto/data.
- Relatorios basicos por status/data/prefixo.
- Importador de exportacao Access JSONL.
- Tabelas raw `legacy_*` e audit de importacao.

### 6.2 Gaps confirmados

| Area | Gap | Impacto |
|---|---|---|
| Financeiro | Falta tela global de contas a receber. | Alto: usuario nao consegue trabalhar recebimentos como no legado. |
| Financeiro | Falta baixa por cliente/titulo com historico de pagamentos. | Alto: legado baixa titulos por cliente. |
| Financeiro | Falta model/tela de movimento de contas/caixa. | Alto: modulo financeiro percebido como ausente. |
| Financeiro | Falta reconciliacao entre `pagar`, `movimento` e recebiveis. | Alto: risco de saldo incorreto. |
| Locacoes | Falta editar/cancelar/excluir locacao. | Alto: legado tinha exclusao e ajustes. |
| Locacoes | Falta imprimir contrato. | Alto: contrato e central no atendimento. |
| Locacoes | Parcelas nao sao geradas no fluxo de criacao da locacao. | Medio/alto: legado cria titulos ao fechar locacao. |
| Locacoes | Campo `usar` nao tem campo proprio. | Medio: informacao operacional aparece em contrato/relatorios. |
| Movimentacao | Retirada/devolucao sao por locacao, sem visao por data/lista. | Medio/alto: legado tinha produtos a retirar/retirados. |
| Relatorios | Relatorios Crystal essenciais nao foram equivalidos. | Alto: financeiro e operacao dependem deles. |
| Clientes | Falta historico do cliente. | Alto: legado tinha tela dedicada. |
| Catalogo | Duplicidade `(prefixo, codigo)` tolerada, mas disponibilidade usa `.first()`. | Alto: pode consultar o produto errado. |
| Catalogo | Prefixos placeholder sem saneamento. | Medio: afeta busca e relatorios. |
| Permissoes | Permissao por modulo e menos granular que `programas`. | Medio: exclusoes/baixas precisam controle fino. |
| Importacao | Falta relatorio de validacao pre-importacao e pos-importacao detalhado. | Alto: migracao fica dificil de auditar. |
| UI | Financeiro nao aparece como area central na navegacao; acesso depende da locacao. | Alto: fluxo diferente do legado. |

---

## 7. Requisitos de refatoracao por dominio

### 7.1 Migracao e saneamento de dados

#### RF-RM-01: Manter `brcom/` fora do Git

- A pasta `brcom/` deve permanecer ignorada por `.gitignore`.
- O snapshot deve ser tratado como insumo local de migracao, nao como codigo-fonte do produto.
- Nenhum `.mdb`, `.exe`, `.dll`, `.ocx`, `.rpt`, `.ldb` ou dados sensiveis do legado deve ser commitado.

#### RF-RM-02: Criar comando de diagnostico do legado

Criar comando Django ou script controlado que leia `var/legacy_export` e produza um relatorio Markdown/JSON com:

- Contagem por tabela.
- Campos e tipos detectados.
- Datas min/max por tabela critica.
- Registros com datas fora da faixa configuravel.
- Chaves orfas.
- Prefixos sem categoria.
- Produtos duplicados por `(prefixo, codigo)`.
- Titulos financeiros sem locacao em `locado`.
- Movimentos sem cliente.
- Movimentos vinculados a `pagar`.
- Totais financeiros por status.

Aceite:

- O comando nao altera o banco.
- O relatorio pode ser anexado a uma migracao/virada.
- O comando falha com mensagem clara se arquivos exportados estiverem ausentes.

#### RF-RM-03: Validar exportacao Access 97

O fluxo oficial deve documentar que o banco Access 97/Jet 3.5 exige Windows PowerShell 32-bit ou ambiente equivalente.

Ajustes no `tools/legacy_migration/export_access.ps1`:

- Atualizar o `SourceMdb` default para uma orientacao neutra, evitando path pessoal.
- Validar existencia do `.mdb`.
- Validar que a conexao abre antes de criar diretorios de saida.
- Incluir hash SHA-256 do `.mdb` no manifest.
- Incluir versao do exportador no manifest.
- Incluir lista de tabelas exportadas e contagens.
- Opcional: exportar tambem queries/views se existirem.

#### RF-RM-04: Reprocessamento idempotente

O importador deve suportar repeticao controlada:

- `--reset` deve ser explicito e registrar backup.
- Deve haver `--dry-run` para validar sem gravar.
- Deve haver `--no-placeholders` para falhar em vez de criar dados artificiais.
- Deve haver `--allow-placeholders` com relatorio de tudo que foi criado.
- Deve haver versao de importador gravada em `legacy_import_audit`.

#### RF-RM-05: Saneamento de datas

Criar politica formal para datas invalidas:

- Datas antes de 1900 ou depois de 2035 devem ser marcadas como suspeitas.
- O importador nao deve converter silenciosamente datas suspeitas em datas operacionais.
- Para registros historicos financeiros, preservar data original em campo de auditoria/raw e definir data operacional conforme regra aprovada.
- Para locacoes, bloquear importacao de grupos sem data valida minima de retirada/retorno.

Casos encontrados:

- `pagar`: 13 vencimentos antes de 1900 e 3 depois de 2035.
- `locado`: 27 linhas com datas fora da faixa normal.
- `locado.dev_efetiva`: 533 nulos.

#### RF-RM-06: Estrategia para placeholders

Hoje o importador cria:

- 1 cliente placeholder.
- 16 categorias placeholder.
- 13 produtos placeholder.

Requisito:

- Todo placeholder deve ter flag/campo de origem ou tag clara.
- Listagens de manutencao devem permitir localizar placeholders.
- O usuario administrador deve poder corrigir placeholders antes da virada final.
- O relatorio pos-importacao deve listar contagem e identificadores tecnicos.

#### RF-RM-07: Preservar dados brutos auditaveis

Manter tabelas `legacy_*` ou alternativa equivalente para auditoria:

- `legacy_clientes`, `legacy_locado`, `legacy_pagar`, `legacy_movimento`, etc.
- Nao usar essas tabelas em telas operacionais comuns.
- Usar apenas em relatorios de reconciliacao, suporte e auditoria.

---

### 7.2 Clientes

#### RF-CL-01: Historico do cliente

Implementar tela "Historico do Cliente", equivalente ao legado.

Deve exibir:

- Dados cadastrais resumidos.
- Locações do cliente.
- Itens locados por locacao.
- Retiradas/devolucoes.
- Recebiveis do cliente.
- Pagamentos/movimentos vinculados.
- Saldo atual em aberto.
- Totais historicos: total locado, total pago, total em aberto, total de multa/juros.

Filtros:

- Periodo.
- Status da locacao.
- Status financeiro.
- Prefixo/codigo do produto.

Aceite:

- A tela deve abrir a partir do detalhe/lista de clientes.
- Nao pode expor clientes sem permissao de modulo.
- Deve usar `select_related`/`prefetch_related` para evitar N+1.

#### RF-CL-02: Busca operacional aprimorada

A lista de clientes deve buscar por:

- Nome.
- CPF.
- RG.
- Telefone/celular.
- Numero legado/id.

O formulario de locacao deve ter busca/autocomplete de cliente, pois a base tem quase 19 mil clientes.

#### RF-CL-03: Exclusao segura de cliente

O legado excluia cliente e registros de locacao/financeiro em cascata. No sistema novo isso deve ser modernizado:

- Excluir cliente com historico deve ser bloqueado por padrao.
- Criar opcao de inativar cliente, se necessario.
- Exclusao fisica so com permissao fina e confirmacao explicita.
- Registrar auditoria de exclusao/inativacao.

---

### 7.3 Catalogo

#### RF-CT-01: Resolver produto duplicado em disponibilidade

Problema atual:

- `Product` permite duplicidade `(category, code)` por compatibilidade.
- `AvailabilityView` usa `.first()`, podendo consultar o produto errado.

Requisito:

- Quando houver mais de um produto para prefixo/codigo, mostrar uma tela de desambiguacao.
- Exibir descricao, cor, tamanho, observacao e historico recente.
- Permitir selecionar o produto correto.
- Opcional: criar campo `legacy_duplicate_group`.

Aceite:

- Nenhuma consulta de disponibilidade deve esconder duplicidade.
- Teste cobrindo dois produtos com mesmo prefixo/codigo.

#### RF-CT-02: Saneamento de prefixos sem categoria

Prefixos sem categoria encontrados em produtos:

`ABO`, `ANEL`, `BOL`, `BRBR`, `BRI`, `CAS`, `CV`, `CVI`, `FL`, `LM`, `MC`, `NEL`, `SAI`, `SUI`, `SUS`, `VEST`.

Prefixos sem categoria encontrados em locacoes:

`BOL`, `BRI`, `CAS`, `CV`, `CVI`, `FL`, `MC`, `NEL`, `SAI`, `SUI`, `SUS`.

Requisito:

- Criar tela/relatorio de manutencao para revisar prefixos placeholder.
- Permitir mesclar prefixo placeholder com categoria existente.
- Atualizar produtos e itens relacionados de forma transacional.

#### RF-CT-03: Busca avancada de produtos

Adicionar filtros:

- Prefixo.
- Codigo.
- Descricao.
- Cor.
- Tamanho.
- Disponibilidade em data.
- Apenas placeholders.
- Apenas duplicados.

#### RF-CT-04: Valor do produto vs valor locado

O campo `produtos.valor` no legado parece pouco confiavel ou subutilizado; o valor efetivo da operacao esta em `locado.valor`.

Requisito:

- Manter `Product.value` como valor sugerido.
- Ao inserir item na locacao, preencher valor inicial a partir do produto, mas permitir ajuste.
- Preservar historico do valor locado em `RentalItem.value`.

---

### 7.4 Locacoes

#### RF-LO-01: Editar locacao

Implementar edicao de locacao com itens.

Regras:

- Permitir editar dados basicos enquanto status `pending`.
- Permitir ajustar observacoes e campos administrativos apos retirada/devolucao com permissao fina.
- Bloquear alteracao destrutiva de itens se houver recebimentos/pagamentos vinculados, salvo rotina administrativa.
- Recalcular total ao alterar itens.

#### RF-LO-02: Cancelar locacao

Adicionar status `cancelled`.

Regras:

- Cancelamento deve preservar historico.
- Cancelamento deve exigir motivo.
- Se houver recebiveis, exigir acao financeira: manter credito, estornar, quitar, cancelar titulos ou bloquear.
- Disponibilidade deve considerar locacao cancelada como nao bloqueante.

Observacao: o template de detalhe ja trata `cancelled`, mas o model ainda nao tem esse status.

#### RF-LO-03: Excluir locacao com seguranca

O legado tinha `EXCLUIR LOCACAO` e apagava `locado` + `pagar`.

No Django:

- Preferir cancelamento.
- Exclusao fisica apenas para locacao sem retirada, sem devolucao e sem pagamento.
- Exigir permissao fina `rentals.delete`.
- Registrar auditoria.

#### RF-LO-04: Contrato de locacao imprimivel

Implementar geracao de contrato em HTML print-friendly e/ou PDF.

Conteudo minimo:

- Dados da empresa: nome, endereco, cidade, CNPJ, telefones.
- Numero da locacao.
- Dados do cliente: nome, endereco, bairro, cidade, RG, CPF, telefones.
- Data de retirada.
- Data prevista de devolucao.
- Itens: prefixo, codigo, descricao, valor, uso/observacao.
- Parcelas/vencimentos.
- Valor total.
- Multa/juros.
- Clausulas contratuais.
- Campo de assinatura do locador/locatario.
- Opcao de imprimir duas vias.

Clausulas legadas detectadas:

- Devolucao ate a data contratada.
- Multa moratoria de 2% sobre valor total.
- Juros de mora de 1% ao mes e correcao monetaria pelo INPC/IBGE.
- Penalidades por danos, perda, nao devolucao em ate sete dias, desistência e troca.
- Possibilidade de credito em caso de troca/desistencia conforme texto do relatorio atual.

Requisito adicional:

- Mover clausulas para configuracao editavel ou template versionado.
- Registrar versao do contrato usada na locacao.

#### RF-LO-05: Campo "usar"

No legado, `locado.usar` aparece em relatorios/contrato.

Adicionar campo em `Rental`:

- `use_for` ou `event_description`.
- Label pt-BR: "Uso/evento" ou "Usar em".
- Migrar valores unicos de `locado.usar` para este campo ou manter em notes com prefixo claro.

#### RF-LO-06: Limite e validacao de itens

O legado indica limite maximo de 15 produtos por locacao.

Decisao de produto:

- Confirmar se o limite ainda e necessario.
- Se sim, aplicar limite no formset e validar no backend.
- Se nao, documentar remocao e garantir contrato suporta mais itens.

#### RF-LO-07: Integrar parcelas na criacao da locacao

No legado, ao fechar locacao sao criados registros em `pagar`.

No Django:

- O fluxo de nova locacao deve permitir definir entrada e parcelas antes de finalizar.
- Gerar recebiveis na mesma transacao da locacao.
- Opcao de imprimir contrato apos salvar.
- Opcao de registrar pagamento de entrada imediatamente, gerando movimento financeiro.

---

### 7.5 Retirada e devolucao

#### RF-MV-01: Visao por data de produtos a retirar

Implementar tela equivalente a "Produtos a Retirar".

Filtros:

- Data inicial/final.
- Apenas nao retirados.
- Cliente.
- Prefixo/codigo.

Acoes:

- Abrir locacao.
- Registrar retirada.
- Marcar retirada em lote com confirmacao.

#### RF-MV-02: Visao por data de produtos retirados

Implementar tela equivalente a "Produtos Retirados".

Filtros:

- Data de retirada.
- Previsao de devolucao.
- Atrasados.
- Cliente.
- Produto.

#### RF-MV-03: Devolucao com baixa financeira

O executavel mostra fluxo:

- Localiza locacao devolvida.
- Calcula valor a pagar da locacao.
- Solicita "INFORME O VALOR PAGO DESTA LOCACAO".

Requisito:

- Na devolucao, mostrar saldo financeiro da locacao.
- Permitir registrar pagamento no mesmo fluxo, se houver saldo.
- Se houver atraso, calcular multa/juros e decidir se cria novo recebivel ou adiciona ao existente.
- Registrar movimento de caixa para pagamento recebido.

#### RF-MV-04: Acerto de devolucoes

O legado tinha "ACERTA DEVOLUCOES".

Requisito:

- Criar rotina de manutencao para localizar inconsistencias:
  - Rental returned sem `Return`.
  - `Return` sem status returned.
  - `Pickup` ausente em locacao returned.
  - Datas efetivas antes da retirada.
  - Itens ativos em locacao devolvida.

---

### 7.6 Financeiro

Este e o maior gap funcional.

#### RF-FI-01: Tela central do financeiro

Criar entrada de menu "Financeiro" visivel para usuarios com permissao.

Indicadores:

- Total em aberto.
- Titulos vencidos.
- Titulos vencendo hoje.
- Titulos a vencer em 7 dias.
- Recebido hoje.
- Recebido no mes.
- Quantidade de titulos em aberto.

Listas principais:

- Contas a receber em aberto.
- Vencidos.
- Recebidos recentemente.
- Movimentos de caixa recentes.

#### RF-FI-02: Contas a receber por vencimento

Equivalente a `receber.rpt`.

Filtros:

- Periodo de vencimento.
- Status: em aberto, quitado, todos.
- Cliente.
- Locacao.
- Apenas vencidos.

Colunas:

- Vencimento.
- Cliente.
- Locacao.
- Valor.
- Valor ja pago.
- Saldo.
- Ultimo pagamento.
- Dias em atraso.
- Juros.
- Total com juros.

Aceite:

- Por padrao, mostrar titulos em aberto (`balance > 0`).
- Exportar/visualizar impressao.

#### RF-FI-03: Contas a receber por cliente

Equivalente a `receberc.rpt`.

Fluxo:

- Buscar cliente.
- Listar titulos do cliente.
- Mostrar saldo total.
- Permitir baixa de um ou mais titulos.

#### RF-FI-04: Baixa de titulo com historico

O modelo atual acumula `paid_amount`, mas nao registra eventos de pagamento.

Criar model sugerido:

```text
Payment
- receivable FK
- customer FK redundante opcional para consultas
- rental FK redundante opcional
- payment_date
- amount
- interest_amount
- discount_amount
- method
- notes
- user FK
- legacy_movement_id nullable
- created_at / updated_at
```

Regras:

- Toda baixa nova cria `Payment`.
- `Receivable.paid_amount` vira soma derivada ou cache recalculavel.
- `Receivable.balance` deve ser recalculado a partir de `amount - sum(payments)`.
- Pagamento parcial permitido.
- Pagamento maior que saldo exige confirmacao e tratamento como credito/troco.
- Estorno deve criar movimento reverso, nao apagar pagamento silenciosamente.

#### RF-FI-05: Movimento de contas/caixa

Criar model para normalizar `movimento`.

Model sugerido:

```text
CashAccount
- name
- active
- legacy_code

FinancialMovement
- date
- account FK
- direction: inflow/outflow
- customer FK nullable
- receivable FK nullable
- rental FK nullable
- amount
- description
- source: manual/payment/import/adjustment
- legacy_id nullable unique
- created_by FK nullable
- created_at / updated_at
```

Regras:

- Baixa de recebivel deve gerar movimento de entrada.
- Estorno deve gerar movimento inverso.
- Movimento manual deve exigir permissao.
- Importacao deve associar `movimento.partida` a `Receivable.legacy_id` quando possivel.
- Preservar movimentos sem cliente como movimentos manuais/importados.

#### RF-FI-06: Reconciliacao financeira

Criar rotina que compare:

- Soma de `Receivable.amount`.
- Soma de `Payment.amount`.
- Soma de `Receivable.balance`.
- Soma de `FinancialMovement.amount` por entrada/saida.
- Titulos com pagamento mas sem movimento.
- Movimentos com `partida` sem titulo.
- Titulos quitados sem valor pago no legado (`pago=0` e `valor_pago=0`).

Resultado:

- Relatorio para administracao.
- Botao de recalculo controlado.
- Exportacao CSV opcional.

#### RF-FI-07: Semantica legada de `pagar.pago`

Documentar e testar:

- `pago=1` legado => em aberto.
- `pago=0` legado => encerrado/quitado.

Aceite:

- Teste unitario do importador cobrindo os dois casos.
- Teste com pagamento parcial (`pago=1`, `valor_pago > 0`, `valor_pago < valor`).
- Teste com titulo quitado legado mas `valor_pago=0`.

#### RF-FI-08: Contas a pagar e fornecedores

O executavel contem menus `CONTAS A PAGAR`, `FORNECEDORES` e `POR FORNECEDOR`, mas o banco analisado nao tem tabelas claras de fornecedores/contas a pagar.

Decisao recomendada:

1. Tratar como descoberta pendente, nao implementar imediatamente como certeza.
2. Validar com usuarios se a loja usava essa rotina.
3. Se confirmado, criar modulo separado de despesas/fornecedores.
4. Se nao confirmado, manter fora do escopo da virada e registrar como backlog futuro.

Possivel model futuro:

```text
Supplier
Payable
ExpensePayment
```

#### RF-FI-09: Juros e multa

Hoje:

- `billing.services.compute_interest` calcula juros simples por dia sobre saldo.
- `movements.services.compute_penalty` calcula multa de devolucao como dias * `Rental.penalty_value`.

Legado/contrato:

- Empresa tem `juros`.
- Contrato cita multa moratoria de 2%, juros de 1% ao mes, INPC/IBGE e penalidades de 50%/100%.
- `locado.multa` tem valores muito altos no agregado, precisa interpretacao.

Requisito:

- Separar conceitos:
  - Multa de atraso na devolucao.
  - Juros financeiro por vencimento.
  - Multa moratoria financeira.
  - Penalidade por dano/perda/desistencia/troca.
- Configurar regras em `Company` ou nova tabela `FinancialSettings`.
- Evitar uma unica taxa generica para todos os casos.

---

### 7.7 Relatorios

#### RF-RP-01: Relatorio de produtos a retirar

Equivalente a Crystal `locados.rpt` com titulo "ROUPAS A SEREM RETIRADAS NO DIA".

Filtros:

- Data inicial/final de retirada.
- Status nao retirado.
- Cliente.
- Produto.

#### RF-RP-02: Relatorio de produtos retirados

Equivalente a Crystal com titulo "ROUPAS RETIRADAS NO DIA".

Filtros:

- Periodo de retirada.
- Status retirado.
- Incluir devolvidos opcional.

#### RF-RP-03: Relatorio de devolvidos

Filtros:

- Periodo de devolucao efetiva.
- Cliente.
- Produto.

#### RF-RP-04: Relatorio de nao devolvidos / atrasados

Filtros:

- Data prevista.
- Apenas locacoes nao devolvidas.
- Dias em atraso.

#### RF-RP-05: Relatorio de vendas/locacoes realizadas

Equivalente a `vendas.rpt`.

Filtros:

- Periodo de retirada.
- Cliente.
- Produto.
- Usuario, se houver auditoria futura.

Colunas:

- Locacao.
- Cliente.
- Retirada.
- Retorno.
- Total.
- Status.

#### RF-RP-06: Relatorios financeiros

Implementar:

- Contas a receber por vencimento.
- Contas a receber por cliente.
- Recebimentos por periodo.
- Movimento de caixa por periodo.
- Inadimplencia/vencidos.

#### RF-RP-07: Impressao e exportacao

Para cada relatorio operacional/financeiro:

- Versao HTML print-friendly.
- Exportacao CSV quando fizer sentido.
- Cabecalho com empresa e filtros usados.
- Data/hora de emissao.

---

### 7.8 Usuarios e permissoes

#### RF-US-01: Permissoes granulares

Manter modulos amplos para navegacao, mas adicionar permissoes de acao.

Sugestao:

| Permissao | Origem legado |
|---|---|
| `customers.view` / `customers.add` / `customers.change` | `bvscad` |
| `customers.delete` | `bvsexcc` |
| `rentals.view` / `rentals.add` / `rentals.change` | fluxo locacao |
| `rentals.delete` | `bvsexcl` |
| `movements.pickup` | `bvsretira` |
| `movements.return` | `bvsdevolve` |
| `billing.view` | `receber`, `receberc` |
| `billing.receive` | `bvsbaixa` |
| `billing.delete_receivable` | `exctit` |
| `cash.view` / `cash.add` | `bvscaixa` |
| `reports.view` | `bvsrel`, `vendas` |
| `users.manage` | `MNUUSU`, `MNUBLOQ` |

#### RF-US-02: Importar permissoes legadas como referencia

Como ha apenas 1 usuario legado, nao migrar senha.

Mas:

- Importar `programas` e `libera` para tabela raw/audit.
- Criar administradores Django pelo fluxo atual.
- Permitir configurar permissoes manualmente.

#### RF-US-03: Auditoria de acoes sensiveis

Registrar:

- Login administrativo.
- Criacao/alteracao/exclusao de cliente/produto/locacao.
- Cancelamento de locacao.
- Baixa/estorno/exclusao de titulo.
- Movimento manual de caixa.
- Alteracao de configuracoes financeiras.

---

### 7.9 Manutencao

#### RF-MT-01: Painel de qualidade da importacao

Adicionar cards:

- Placeholders pendentes.
- Produtos duplicados.
- Prefixos placeholder.
- Recebiveis com datas suspeitas.
- Locações financeiras sem itens.
- Movimentos sem cliente.
- Movimentos sem titulo correspondente.

#### RF-MT-02: Rotinas controladas

Rotinas:

- Recalcular totais de locacoes.
- Recalcular saldos de recebiveis.
- Reconciliar pagamentos e movimentos.
- Recriar pickups/returns a partir de rentals importadas.
- Mesclar categorias/prefixos.
- Marcar dados suspeitos como revisados.

Todas devem:

- Exigir permissao `maintenance`.
- Rodar em transacao quando alterarem dados.
- Mostrar previa quando possivel.
- Registrar auditoria.

---

### 7.10 UX e produtividade operacional

#### RF-UX-01: Fluxo de balcão para nova locacao

O fluxo ideal:

1. Buscar/criar cliente sem sair da locacao.
2. Definir retirada/devolucao/uso.
3. Adicionar produtos com busca rapida por prefixo+codigo.
4. Validar disponibilidade de cada item automaticamente.
5. Informar valor/multa.
6. Definir entrada e parcelas.
7. Salvar locacao, gerar recebiveis e imprimir contrato.

#### RF-UX-02: Atalhos e densidade

Telas operacionais devem priorizar:

- Tabelas densas e legiveis.
- Acoes primarias visiveis.
- Busca por teclado.
- Menos navegacao entre modulos.
- Feedback claro em pt-BR.

#### RF-UX-03: Mascara e validacao de campos brasileiros

Aplicar onde fizer sentido:

- CPF.
- CNPJ.
- Telefone/celular.
- CEP se adicionado futuramente.
- Datas.
- Valores monetarios.

Manter validacao permissiva para dados legados antigos, mas orientar dados novos.

---

## 8. Backlog priorizado

### P0 - Obrigatorio antes de virada operacional

| Item | Descricao | Criterio de aceite |
|---|---|---|
| P0-01 | Tela central de Financeiro. | Usuario acessa recebiveis sem entrar por locacao. |
| P0-02 | Contas a receber por vencimento. | Equivalente funcional ao `receber.rpt`. |
| P0-03 | Contas a receber por cliente e baixa. | Usuario baixa titulo pelo cliente. |
| P0-04 | Historico de pagamentos. | Cada baixa nova gera registro auditavel. |
| P0-05 | Movimento de caixa. | Baixas geram entrada; movimentos importados consultaveis. |
| P0-06 | Contrato de locacao imprimivel. | Locacao pode gerar contrato com dados, itens, parcelas e clausulas. |
| P0-07 | Editar/cancelar locacao. | Ajuste operacional possivel sem apagar historico. |
| P0-08 | Validacao de disponibilidade com duplicados. | Consulta nao usa produto errado silenciosamente. |
| P0-09 | Relatorio de qualidade da importacao. | Admin ve placeholders, duplicados, orfaos e datas suspeitas. |
| P0-10 | Testes de importacao financeira. | Cobrir `pagar.pago=1` aberto e `pago=0` quitado. |

### P1 - Forte recomendacao para estabilidade

| Item | Descricao | Criterio de aceite |
|---|---|---|
| P1-01 | Historico do cliente. | Cliente mostra locacoes, itens, recebiveis e pagamentos. |
| P1-02 | Relatorios a retirar/retirados/devolvidos/atrasados. | Equivalentes aos Crystal operacionais. |
| P1-03 | Relatorio de vendas/locacoes. | Equivalente ao `vendas.rpt`. |
| P1-04 | Permissoes granulares para exclusao/baixa/caixa. | Usuario sem permissao nao executa acao sensivel. |
| P1-05 | Reconciliacao financeira. | Totais de recebiveis, pagamentos e movimentos fecham ou exibem divergencias. |
| P1-06 | Saneamento assistido de categorias/produtos placeholder. | Admin consegue corrigir sem SQL manual. |
| P1-07 | Busca/autocomplete de cliente e produto. | Fluxo de locacao fica rapido com base grande. |
| P1-08 | Impressao/exportacao de relatorios. | Relatorios podem ser impressos ou exportados. |

### P2 - Pos-virada / melhoria continua

| Item | Descricao |
|---|---|
| P2-01 | Contas a pagar e fornecedores, se uso for confirmado. |
| P2-02 | Metodos de pagamento e conciliacao por forma. |
| P2-03 | Dashboard financeiro mensal. |
| P2-04 | Auditoria completa de alteracoes cadastrais. |
| P2-05 | Campo de inativacao para clientes/produtos. |
| P2-06 | Exportacoes CSV avancadas. |
| P2-07 | Parametrizacao completa das clausulas contratuais. |

---

## 9. Plano de migracao recomendado

### Fase 1 - Congelar e diagnosticar

1. Confirmar qual `.mdb` e a fonte oficial.
2. Rodar exportacao Access com manifest/hash.
3. Rodar diagnostico sem gravar.
4. Revisar relatorio de problemas com usuario-chave.
5. Definir politica para:
   - Datas invalidas.
   - Titulos sem locacao em `locado`.
   - Produtos duplicados.
   - Produtos/categorias placeholder.
   - Recebiveis quitados sem `valor_pago`.

### Fase 2 - Ajustar modelo alvo

1. Criar `Payment`.
2. Criar `CashAccount` e `FinancialMovement`.
3. Adicionar status `cancelled` em `Rental`.
4. Adicionar campo `use_for` em `Rental`.
5. Adicionar campos de auditoria/importacao quando necessario:
   - `legacy_id`.
   - `legacy_source`.
   - `legacy_notes`.
   - flags de placeholder.

### Fase 3 - Reimportacao em homologacao

1. Limpar base de homologacao.
2. Importar dados brutos.
3. Importar dados normalizados.
4. Gerar relatorio pos-importacao.
5. Comparar contagens e totais:
   - Clientes.
   - Produtos.
   - Locações.
   - Itens.
   - Recebiveis.
   - Pagamentos.
   - Movimentos.
6. Validar amostras manuais com usuario.

### Fase 4 - Validacao operacional

Roteiros de teste com usuario:

- Criar locacao nova com parcelas e contrato.
- Consultar disponibilidade de produto comum e duplicado.
- Registrar retirada.
- Registrar devolucao com atraso.
- Baixar titulo parcial e total.
- Conferir caixa do dia.
- Gerar contas a receber por vencimento.
- Gerar contas a receber por cliente.
- Consultar historico de cliente.
- Cancelar locacao.

### Fase 5 - Virada

1. Fazer backup do `.mdb` final.
2. Exportar com hash.
3. Importar em producao.
4. Rodar diagnostico pos-importacao.
5. Conferir totais financeiros.
6. Bloquear uso do legado ou deixa-lo somente leitura.
7. Registrar data/hora da virada.

---

## 10. Riscos e mitigacoes

| Risco | Severidade | Mitigacao |
|---|---|---|
| Interpretar errado `pagar.pago`. | Alta | Testes de importacao e documentacao da semantica: 1 = aberto, 0 = encerrado. |
| Perder historico de pagamentos. | Alta | Criar `Payment` e importar `movimento` quando vinculado. |
| Saldos divergentes pos-importacao. | Alta | Reconciliacao financeira automatizada. |
| Produtos duplicados causarem disponibilidade incorreta. | Alta | Desambiguacao obrigatoria. |
| Contrato novo nao refletir clausulas antigas. | Alta | Implementar contrato versionado e validar texto com responsavel legal/administrativo. |
| Datas corrompidas afetarem cobranca. | Alta | Relatorio de datas suspeitas e politica aprovada. |
| Excluir dados historicos por engano. | Alta | Preferir cancelamento/inativacao; permissoes finas; auditoria. |
| Importacao depender de driver antigo. | Media | Documentar ambiente 32-bit e manter exportacao JSONL como fronteira. |
| Menus legados residuais virarem escopo indevido. | Media | Validar uso real antes de implementar fornecedores/contas a pagar. |
| SQLite crescer com fotos binárias. | Media | Avaliar armazenamento em arquivo para fotos futuras; manter atual se volume baixo. |

---

## 11. Criterios gerais de aceite

O sistema so deve ser considerado substituto operacional do legado quando:

- O financeiro puder ser usado sem navegar por locacao individual.
- Contas a receber por vencimento e por cliente estiverem funcionais.
- Baixas gerarem historico e movimento de caixa.
- Contrato de locacao puder ser impresso com dados e clausulas completas.
- Historico do cliente estiver disponivel.
- Disponibilidade lidar corretamente com produtos duplicados.
- Relatorios operacionais essenciais existirem.
- Importacao gerar relatorio de validacao antes e depois.
- Totais financeiros forem reconciliados ou divergencias forem listadas.
- Permissoes impedirem exclusoes/baixas indevidas.
- Testes cobrirem regras financeiras, migracao e fluxos criticos.

---

## 12. Questoes em aberto para validacao com usuario

1. A rotina "Contas a Pagar" era usada de fato ou era menu residual?
2. Havia cadastro de fornecedores em outra base/versao?
3. `locado.multa` representa multa diaria, multa total ou valor juridico de penalidade?
4. A loja ainda usa limite de 15 itens por locacao?
5. O contrato atual deve manter exatamente as clausulas do Crystal ou sera revisado?
6. Titulos `pagar` sem locacao em `locado` devem aparecer como locacoes financeiras, saldos avulsos ou historico arquivado?
7. Recebiveis quitados no legado com `valor_pago=0` devem exibir valor pago igual ao valor do titulo no novo sistema?
8. Movimentos sem cliente devem entrar como caixa manual?
9. Fotos de comprovacao por item devem permanecer no banco ou migrar para arquivos em disco antes de producao?
10. Usuarios novos devem ser recriados manualmente ou importados de alguma lista externa?

---

## 13. Recomendacao tecnica imediata

Ordem sugerida de implementacao:

1. Criar models de `Payment`, `CashAccount` e `FinancialMovement`.
2. Ajustar importador para popular pagamentos/movimentos e registrar relatorio de reconciliacao.
3. Criar tela central de Financeiro com contas a receber por vencimento.
4. Criar baixa por cliente/titulo.
5. Criar contrato imprimivel.
6. Adicionar status `cancelled`, edicao segura e campo `use_for` em locacao.
7. Corrigir disponibilidade para duplicados.
8. Criar historico do cliente.
9. Expandir relatorios operacionais.
10. Adicionar permissoes granulares e auditoria para acoes sensiveis.

Essa ordem reduz o maior risco primeiro: usar o sistema novo sem equivalencia financeira com o legado.

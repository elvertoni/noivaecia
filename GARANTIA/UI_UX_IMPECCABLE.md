# Auditoria UI/UX com Impeccable

Data: 2026-06-19

## Escopo

Auditoria orquestrada com especialistas em:

- UI/UX de produto operacional
- Formularios, campos e acessibilidade
- QA, testes e performance

Referencial usado: produto operacional para Noivas & Cia, com foco em clareza, rapidez de balcão, consistencia visual, textos em PT-BR, acessibilidade e baixo risco de erro em fluxos financeiros, locacao, movimentacao e catalogo.

## Resultado da auditoria

Nenhum P0 encontrado.

Principais riscos encontrados:

- Inconsistencias em disponibilidade visual de itens de locacao.
- Risco de interpretacao financeira incorreta em juros e pagamento vencido.
- Problemas de acessibilidade em controles customizados e labels.
- Responsividade fraca em acoes de formulario e tabelas operacionais largas.
- Pontos de performance em consultas, exports e historicos sem paginacao.

## Corrigido neste lote

### Locacoes e catalogo

- Disponibilidade no formulario de locacao passou a validar o intervalo completo de retirada e retorno, nao apenas a data de retirada.
- Modal "Buscar com filtros" tambem passou a usar o intervalo completo da locacao.
- Endpoint `catalog:availability_json` aceita `pickup_date` e `return_date`, mantendo compatibilidade com o parametro antigo `date`.
- Testes adicionados para conflito parcial dentro do periodo de locacao.
- Detalhe da locacao agora exibe descricao do produto e complemento do item locado, alinhado ao contrato.
- Acao destrutiva no detalhe da locacao foi renomeada de "Cancelar" para "Cancelar locacao".
- Cards recolhidos de itens da locacao foram trocados de `div onclick` para `button type="button"`, com semantica e foco por teclado.
- Autocomplete visivel de cliente/produto recebeu `aria-required`, `aria-invalid` e associacao de erro quando aplicavel.
- Prefetch de itens em detalhe/contrato de locacao passou de `product` para `product__category`, evitando N+1 ao renderizar codigo/categoria.

### Financeiro

- Coluna "Juros" em recebimentos por cliente deixou de repetir saldo e passou a exibir juros calculados.
- Confirmacao de pagamento acima do valor esperado agora compara contra o total com juros, nao apenas contra o saldo principal.
- Checkboxes da baixa multipla receberam `aria-label` contextual com locacao, vencimento e saldo.
- Labels em formularios financeiros renderizados por loop passaram a usar `for`.
- Calculos de juros em listas e formularios financeiros passaram a reutilizar a configuracao da empresa carregada uma vez por tela, evitando chamadas repetidas a `Company.load()` por titulo vencido.

### Relatorios

- Exports CSV passaram a usar `StreamingHttpResponse`, evitando montar todo o arquivo em memoria antes da resposta.
- Exports CSV passaram a respeitar o limite configurado do relatorio em vez de remover o limite no download sem filtro.

### Movimentacao

- Campo "Valor recebido agora" na devolucao passou a usar `BRDecimalInput`, aceitando o mesmo padrao decimal brasileiro dos demais fluxos financeiros.
- Labels em formularios de retirada/devolucao passaram a usar `for`.
- Listas operacionais de retirada e devolucao agora usam cards densos no mobile, mantendo a tabela no desktop e deixando a acao principal acessivel sem scroll horizontal.

### Responsividade operacional

- Lista global de titulos a receber agora usa cards densos no mobile, com cliente, locacao, vencimento, saldo, total com juros, atraso e baixa visiveis na primeira varredura.

### Clientes

- Historico de locacoes no detalhe do cliente passou a ser paginado em blocos de 25, preservando filtros ao navegar entre paginas.
- Contador de locacoes passou a usar a contagem do paginator, evitando avaliar o queryset completo no template.

### Formularios e acessibilidade geral

- Labels em formularios de produto, categoria, empresa, mesclagem de categoria, pagamento, estorno e lancamento manual passaram a apontar para o campo correto.
- Help texts criticos passaram a ser exibidos em formularios de produto e empresa.
- `.form-actions` ficou responsivo: empilha no mobile e permite quebra em telas maiores.

## Verificacao executada

Comandos executados:

```powershell
.\venv\Scripts\python.exe manage.py test catalog.tests catalog.tests_r8 rentals.tests movements.tests_r10 billing.tests_r5 billing.tests_r6
```

Resultado: 165 testes, OK.

```powershell
.\venv\Scripts\python.exe manage.py test billing.tests billing.tests_r5 billing.tests_r6
```

Resultado: 97 testes, OK.

```powershell
.\venv\Scripts\python.exe manage.py test reports.tests_r11
```

Resultado: 36 testes, OK.

```powershell
.\venv\Scripts\python.exe manage.py test movements.tests_r10 billing.tests_r5
```

Resultado: 61 testes, OK.

```powershell
.\venv\Scripts\python.exe manage.py test customers.tests customers.tests_r9
```

Resultado: 36 testes, OK.

```powershell
.\venv\Scripts\python.exe manage.py check
```

Resultado: OK.

```powershell
npm run build:css
```

Resultado: OK.

Observacao: o build CSS exibiu apenas aviso de `Browserslist/caniuse-lite` desatualizado.

## Corrigido no segundo lote (2026-06-20)

### Performance de busca

- Filtros de busca por nome de cliente passaram a consultar `name_search` (normalizado + GIN trigram) em vez de `name__icontains` cru em `billing/views.py`, `movements/views.py` e `reports/services.py`.
- Filtros de descricao de produto passaram a consultar `description_search` (normalizado + GIN trigram) em `movements/views.py` e `catalog/views.py`.
- Termo de busca normalizado com o mesmo `_normalize_name` usado para popular as colunas, garantindo casamento de acento/caixa e uso do indice.

### Querystring segura

- Links de paginacao, filtros de status, limpar busca e export CSV passaram a usar a tag nativa `{% querystring %}` (Django 5.2), que preserva os parametros atuais e aplica `urlencode`.
- Caminhos: `templates/customers/customer_list.html`, `templates/catalog/product_list.html`, `templates/billing/receivable_list_global.html`, `templates/reports/a_retirar.html`.
- Elimina quebras de paginacao/export quando filtros contem espaco, `&`, acento ou `+`.

### Movimentacao

- Lista de atrasados em movimentacao passou a ser paginada em blocos de 30 (`OverdueListView`), evitando montar toda a lista em memoria, com navegacao e contagem total no rodape.

## Verificacao do segundo lote

```powershell
.\venv\Scripts\python.exe manage.py test billing movements reports catalog customers
```

Resultado: 251 testes, OK. `manage.py check`: OK. Templates alterados compilam sem erro.

## Edicao de itens da locacao (2026-06-20)

### Causa raiz

- `RentalItem.product` e FK obrigatoria (`on_delete=PROTECT`), entao linhas sem produto nao podem ser persistidas. Os "itens vazios" observados eram formularios de item em branco renderizados expandidos: sobravam no re-render apos erro de validacao e se acumulavam a cada clique em "Adicionar item".
- O campo `value` tem `default=0` no model; isso fazia um formulario em branco parecer "alterado" (`has_changed` True, pois `''` != `0`), entao a linha vazia era validada (falhava em produto obrigatorio) e re-renderizada, alongando a tela.

### Corrigido

- `RentalItemForm.has_changed`: linha nova sem produto e tratada como vazia (ignorada no save e no render), neutralizando o `default=0`.
- `BaseRentalItemFormSet` compartilhado por criacao/edicao:
  - `get_queryset` carrega apenas itens com produto (`product__isnull=False`), com `select_related('product__category')`.
  - `clean` bloqueia produto duplicado recem-adicionado, mantendo duplicatas legadas ja salvas editaveis (compatibilidade).
- Template `rental_form.html`: o loop nao renderiza linhas em branco/nao salvas no re-render (`not items.is_bound or instance.pk or has_changed`), evitando itens vazios intercalados.
- JS: "Adicionar item" reutiliza a unica linha vazia existente em vez de empilhar varias; o mesmo vale para o deep-link `?add=1`.
- Itens salvos continuam colapsados em cartoes compactos (codigo, descricao, valor), expandindo so na edicao — reduz drasticamente a rolagem.

### Verificacao

```powershell
.\venv\Scripts\python.exe manage.py test
```

Resultado: 387 testes, OK. 6 testes novos em `rentals/tests.py::RentalItemEditingTests` cobrindo: 3 itens abrem 3 formularios; form em branco nao cria registro; duplicado novo bloqueado; duplicado legado editavel; remocao intermediaria preserva IDs; linha vazia nao re-renderiza apos erro.

## Backlog restante

### P2

- Padronizar vocabulario de acoes financeiras.
  - Termos atuais misturam "Baixar", "Pagar", "Registrar pagamento", "Confirmar baixa".
  - Direcao sugerida: "Receber titulo", "Receber selecionados", "Registrar recebimento".

- Criar helper unico para renderizacao de campos.
  - Deve padronizar `label for`, obrigatoriedade, `help_text`, `field-error`, `aria-invalid`, `aria-describedby` e `role="alert"` quando houver erro.

- Revisar indicacao visual/programatica de campos obrigatorios.
  - Hoje a indicacao e inconsistente entre telas.

- Revisar foco automatico em cidade no formulario de cliente apos mudar estado.
  - Impacto: pode surpreender usuario de teclado.
  - Direcao: atualizar opcoes sem mover foco ou anunciar mudanca por live region.

## Observacoes do working tree

Durante esta rodada ja existiam alteracoes locais em arquivos fora do lote principal, incluindo:

- `billing/forms.py`
- `catalog/forms.py`
- `company/forms.py`
- `core/ui.py`
- `rentals/forms.py`
- `static/js/app.js`

Essas alteracoes foram preservadas e nao revertidas.

Tambem existem arquivos nao rastreados no working tree, como:

- `db.sqlite3.zip`
- `easypanel-mcp-server-2.0.0.tgz`
- `legado.jpeg`
- `media/`

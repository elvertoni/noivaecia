# Plano de Refatoração da Tela de Locação (`tela-cliente.md`)

**Autor:** Engenheiro de Software Sênior & Especialista em UX de Sistemas Operacionais  
**Data:** 29/07/2026  
**Projeto:** Noivas & Cia (Django 5 Monolith)  
**Alvo:** Formulário de Criação e Edição de Locações (`templates/rentals/rental_form.html`)  

---

## 1. Contexto & Diagnóstico Abrangente da Insatisfação do Cliente

Com base na análise minuciosa dos materiais enviados pelo cliente no diretório `tela-cliente/`:
* **`Imagem1.png`**: Tela do sistema legado em Windows Desktop (FoxPro/VB6) utilizada durante décadas na loja.
* **`WhatsApp Audio 2026-07-29 at 12.07.08.ogg`** (29s): Áudio da atendente explicando o fluxo de adição contínua de itens.
* **`WhatsApp Video 2026-07-29 at 12.07.07.mp4`** (36s): Vídeo mostrando a insatisfação com os "cards separados" no sistema web.
* **`WhatsApp Video 2026-07-29 at 14.04.06.mp4`** (4m08s): Vídeo detalhando a rotina real de atendimento no balcão, regras de entrada, desconto à vista (10%), cálculo de multa de reposição e emissão de contratos impressos.

### 🔴 Principais Problemas Identificados no Sistema Web Atual

1. **Layout de Itens Fragmentado em Cards Verticais ("Separado assim"):**
   * **Reclamação literal da atendente (Vídeo 1):** *"Eu fui fazer um contrato... aí você tem que adicionar o item 1, fiz o item 1. Aí depois o item 2 aqui separado, né? Eu não queria separado assim, eu queria que pudesse colocar tudo um abaixo do outro, depois a descrição e o valor total."*
   * **Diagnóstico:** O sistema web atual renderiza cada item em um card expansível vertical grande com bordas, sombras e botão "Buscar com filtros" individual. Ao adicionar 3 a 5 peças (ex: Paletó, Camisa, Gravata, Calça, Sapato), a página fica excessivamente longa, exigindo rolagem constante do mouse e perdendo a visão global da locação.

2. **Fricção na Adição de Itens Rápida:**
   * **Reclamação literal (Áudio 1):** *"Eu já vou colocando os itens um abaixo do outro, né? Mas esse a cada item que eu coloco tenho que colocar lá adicionar itens e lá embaixo."*
   * **Diagnóstico:** A atendente quer uma experiência de **grade contínua (Data Grid)** estilo planilha/sistema legado, onde o foco no teclado navega fluido e adicionar a próxima peça seja imediato (via `Enter` ou botão compacto na própria tabela).

3. **Cálculos Comerciais Faltantes ou Ocultos no Formulário:**
   * **Desconto à Vista (10%):** Conforme demonstrado no Vídeo 2 (122s), pagamentos à vista possuem desconto de 10% aplicado sobre o subtotal dos itens. O formulário web precisa ter a opção clara de "À Vista (10% Desc.)" recalculando o valor final automaticamente.
   * **Valor Estimado de Multa / Reposição:** Conforme Vídeo 2 (107s), o contrato exige informar o valor total de reposição das peças em caso de extravio/dano (multa).
   * **Regra de Entrada Mínima:** Conforme Vídeo 2 (161s), a loja exige entrada obrigatória (ex: R$ 100, R$ 300) no momento da reserva para evitar desistências no dia do evento.

4. **Visibilidade do Resumo e Totais:**
   * Na tela legada (`Imagem1.png`), o cliente enxergava em um único relance: Lista de peças (Prefixo, Número, Descrição, Valor), Subtotal, Valor da Multa, Entrada e Vencimentos no Histórico.

---

## 2. Visão de UX & Princípios de Design (Impeccable & Product UI)

Para atender à operação real de balcão de uma loja de aluguel de noivas e trajes:

1. **Grade de Alta Densidade (High-Density Grid over Cards):**
   Substituir os blocos de cards verticais por uma **Tabela de Itens Limpa e Compacta**. Cada linha é uma peça com busca rápida inline.
2. **Atendimento Rápido por Teclado (Keyboard First):**
   `Enter` ou `Tab` ao selecionar o produto insere e foca automaticamente a próxima linha vazia sem interromper a digitação.
3. **Painel de Totais e Condições de Pagamento Visível:**
   Calculadora em tempo real com Subtotal, Desconto (%), Multa de Reposição, Entrada e Parcelamento lado a lado com a tabela.
4. **Preservação de Dados & Zero Regressão:**
   Manter todos os campos do Django formset (`product`, `description`, `value`, `proof_photo_upload`, `DELETE`) totalmente funcionais e validados no backend.

---

## 3. Mockup Proposto para a Nova Tela de Locação

```
+---------------------------------------------------------------------------------------------------------+
|  Nova Locação                                                      [ Data Retirada: 23/10/2026 ] [ Retorno: 26/10/2026 ]  |
+---------------------------------------------------------------------------------------------------------+
| CLIENTE: [ Buscar por nome ou CPF... (ex: João Pedro)                ] [+ Novo Cliente]                 |
+---------------------------------------------------------------------------------------------------------+
| ITENS DA LOCAÇÃO                                                        [ + Adicionar Peça (F2) ]       |
+-----+----------------------------------------+-----------------------------------+----------------+-----+
| #   | PEÇA / CÓDIGO (Buscar por código/desc) | OBSERVAÇÃO / COMPLEMENTO          | VALOR (R$)     |     |
+-----+----------------------------------------+-----------------------------------+----------------+-----+
| 01  | BMA39 · BLEIZER ITALIANO SLIM FIT - 64 | Tamanho 64, Ajustar manga 1cm     | R$ 180,00      | [X] |
| 02  | CAM993 · CAMISA SLIM TRAB BRANCA - 49  | Gola 49                           | R$  50,00      | [X] |
| 03  | GMA650 · GRAVATA LISTRA CINZA          |                                   | R$  20,00      | [X] |
| 04  | [ Buscar código ou descrição...      ] | [ Observação opcional...        ] | R$   0,00      | [X] |
+-----+----------------------------------------+-----------------------------------+----------------+-----+
|                                                                     SUBTOTAL DAS PEÇAS: R$ 250,00       |
+---------------------------------------------------------------------------------------------------------+
| CONDIÇÕES DE PAGAMENTO & CONTRATO                                                                       |
+-------------------------------------------------------------------+-------------------------------------+
| [X] Aplicar Desconto à Vista (10%)                               | RESUMO FINANCEIRO                   |
|                                                                   | Subtotal Peças:         R$ 250,00   |
| Entrada Paga Agora:   [ R$ 100,00 ] Forma: [ PIX          v ]    | Desconto (10%):        -R$  25,00   |
| Saldo Restante:       [ R$ 125,00 ] Parcelas: [ 1x      v ]    | VALOR FINAL LOCAÇÃO:    R$ 225,00   |
| Primeiros Vencimento: [ 20/10/2026 ]                              | VALOR MULTA REPOSIÇÃO:  R$ 850,00   |
+-------------------------------------------------------------------+-------------------------------------+
| OBSERVAÇÕES DO CONTRATO:                                                                                |
| [ Retirar na quinta-feira à tarde. Trazer documento original.                                         ] |
+---------------------------------------------------------------------------------------------------------+
| [ Cancelar ]                                           [ Salvar e Imprimir ]  [ Salvar Locação (Ctrl+S) ] |
+---------------------------------------------------------------------------------------------------------+
```

---

## 4. Plano Técnico de Implementação (Passo a Passo)

### 🏗️ Fase 1: Redesenho do HTML/Template (`templates/rentals/rental_form.html`)

1. **Estrutura de Tabela Compacta (`<table class="data-table">`):**
   * Substituir o container `#item-forms` (cards) por uma tabela responsiva dentro de `<div class="table-shell">`.
   * **Colunas:**
     1. `#` (Número do item / `item-index`)
     2. `Produto` (Input de busca autocomplete inline + select oculto)
     3. `Observação/Descrição` (Input compacto)
     4. `Valor (R$)` (Input numérico/decimal alinhado à direita)
     5. `Ações` (Botão de remover linha `[X]` + upload discreto de foto se necessário)

2. **Template de Linha Vazia (`<template id="empty-form">`):**
   * Atualizar o template `<template>` para gerar um elemento `<tr>` em vez de um `<div>` card.

3. **Painel de Totais & Cálculos em Tempo Real:**
   * Adicionar bloco fixo de cálculo do Subtotal, Desconto à Vista (10%), Valor da Multa (soma dos valores de reposição) e Saldo Final.

---

### ⚡ Fase 2: Otimização do JavaScript de Atendimento Rápido (`rental_form.html`)

1. **Adição Fluida de Linhas (Keyboard Workflow):**
   * Ao selecionar um produto via autocomplete ou pressionar `Enter` na coluna de valor da última linha, chamar `addItemRow(true)` automaticamente para abrir a linha `#05` sem que a atendente precise tirar as mãos do teclado.
   * Adicionar atalho de teclado global: `F2` adiciona nova linha; `Ctrl+S` envia o formulário.

2. **Cálculo Automático de Desconto & Totais:**
   * Escutar eventos `input` / `change` nos valores dos itens e atualizar dinamicamente:
     $$\text{Subtotal} = \sum \text{Valor dos Itens}$$
     $$\text{Desconto} = \text{Subtotal} \times 0.10 \quad (\text{se "À Vista" selecionado})$$
     $$\text{Total Final} = \text{Subtotal} - \text{Desconto}$$
     $$\text{Saldo Restante} = \text{Total Final} - \text{Entrada Paga}$$

---

### 💼 Fase 3: Ajustes no Backend Django (`rentals/forms.py` & `rentals/views.py`)

1. **Suporte a Desconto & Multa na Model/Form (`rentals/models.py` & `rentals/forms.py`):**
   * Garantir que `discount_amount` ou flag de pagamento à vista seja devidamente processado na criação/edição.
   * Garantir que a soma do valor dos itens seja salva no campo de multa/reposição quando aplicável.

2. **Opção "Salvar e Imprimir":**
   * Adicionar botão `Salvar e Imprimir` no rodapé fixo. Quando clicado, envia um parâmetro `print=1` na requisição POST, fazendo o Django redirecionar diretamente para a view de comprovante/contrato em formato de impressão.

---

### 🧪 Fase 4: Validação & Suíte de Testes Automatizados

1. **Testes de Formulário & UI (`rentals/tests_footer_ui.py` e `rentals/tests.py`):**
   * Validar criação de locação com 1, 3 e 10 itens na nova tabela.
   * Validar cálculo de entrada, desconto e total final.
   * Garantir que nenhum teste existente de permissão, locação ou itens seja quebrado.

2. **Validação Visual com o Cliente:**
   * Apresentar a nova tela em formato de grade para o cliente e coletar o feedback de agilidade no balcão.

---

## 5. Cronograma de Execução Estimado

| Etapa | Tarefa | Esforço Estimado |
| :--- | :--- | :--- |
| **Fase 1** | Redesenhar HTML/CSS do formset para Tabela de Alta Densidade | 2 horas |
| **Fase 2** | Implementar JS de autocomplete inline, navegação por teclado (`Enter`/`F2`) e calculadora de totais | 2 horas |
| **Fase 3** | Ajustar backend Django para desconto à vista e botão "Salvar e Imprimir" | 1.5 horas |
| **Fase 4** | Atualizar testes automatizados e validar com dados de produção | 1 hora |
| **Total** | **Refatoração completa pronta para release** | **~6.5 horas** |

---

*Este plano foi salvo no diretório raiz como `tela-cliente.md` para consulta e aprovação.*

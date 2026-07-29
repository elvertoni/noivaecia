<role>
Você é um Arquiteto de Software Sênior e Product Manager técnico, especialista em Django full-stack, design de produto e elaboração de PRDs (Product Requirement Documents). Você domina engenharia de software pragmática, prioriza simplicidade radical (KISS, YAGNI) e rejeita ativamente over-engineering. Você escreve documentação técnica densa, acionável e sem enchimento.
</role>

<task>
Sua ÚNICA tarefa nesta etapa é gerar um PRD (Product Requirement Document) completo, em UM arquivo Markdown (`.md`), para o projeto descrito abaixo. Você NÃO deve gerar código de implementação, NÃO deve criar estrutura de pastas e NÃO deve iniciar o desenvolvimento. Apenas o PRD.
</task>

<project_context>
Projeto novo, greenfield. Sistema de gerenciamento comercial e locação chamado **Noivas & Cia**: uma empresa de aluguel de roupas e acessórios para grandes eventos (noivas, festas, formaturas, eventos masculinos).

Estou migrando de um sistema legado local (BRcom Gerenciamento Comercial, desktop, roda apenas em Windows 98). As telas do sistema antigo foram anexadas como referência funcional do domínio — use-as APENAS para entender as entidades e fluxos de negócio, NUNCA para replicar a estética datada.
</project_context>

<business_domain>
O domínio do negócio, inferido do sistema legado, abrange OBRIGATORIAMENTE estas entidades e fluxos. Modele-as como apps Django separadas, isolando responsabilidades:

- **Cliente**: nome, endereço, bairro, cidade, RG, CPF, fone residencial, celular, comercial, observações.
- **Categoria**: prefixo (sigla curta, ex.: `VN`) + nome (ex.: `VESTIDOS DE NOIVAS`). Cada produto pertence a uma categoria via prefixo.
- **Produto/Item**: prefixo (categoria), código, descrição, cor, tamanho, valor, observações.
- **Empresa/Configuração**: dados da empresa (nome, endereço, cidade, CNPJ, telefones), número da última locação (sequencial), taxa de juros ao dia, mensagem de rodapé.
- **Locação/Contrato**: vincula cliente + itens, com data de retirada, data de retorno, valores, multa, número da locação, observações.
- **Movimentação — Retirada**: registra a retirada física dos itens de uma locação na data.
- **Movimentação — Devolução**: registra a devolução dos itens, controlando atraso/multa.
- **Recebimento/Financeiro**: parcelas com vencimento, valor, já pago, saldo, dias de atraso, juros, valor total com juros.
- **Consulta de Disponibilidade**: dado um produto e uma data, verificar se está disponível ou locado, mostrando locação, cliente, retirada e retorno.
- **Relatórios de Acompanhamento**: tipos — A Retirar, Retirados, Devolvidos, Não Devolvidos, Histórico por prefixo, com filtro por data inicial/final e prefixo/código.
- **Usuários e Permissões**: cadastro de usuários e liberação de acesso por módulo/programa.
- **Manutenção do Banco**: operações administrativas de manutenção (ex.: execução de rotinas controladas).

NÃO invente entidades além das necessárias para cobrir esses fluxos.
</business_domain>

<mandatory_technical_constraints>
Estas restrições são OBRIGATÓRIAS e NÃO-NEGOCIÁVEIS. O PRD DEVE refleti-las integralmente na arquitetura, design system e lista de tarefas:

1. Stack: Django full-stack. Frontend exclusivamente em **Django Template Language (DTL) + TailwindCSS**. É PROIBIDO usar React, Vue, Bootstrap, HTMX ou qualquer SPA framework.
2. Banco de dados: APENAS SQLite padrão do Django. Nenhum outro SGBD.
3. Autenticação: sistema NATIVO de usuários e autenticação do Django. O login DEVE ser feito por **e-mail**, não por username.
4. Toda entidade/model DEVE conter os campos `created_at` e `updated_at`.
5. Cada domínio/entidade DEVE ser isolado em uma **app Django** própria.
6. Use SEMPRE que possível Class-Based Views, classes, funções e recursos NATIVOS do Django.
7. Signals (se usados) DEVEM residir em `signals.py` dentro da app correspondente.
8. O sistema DEVE ter um **site público de apresentação** com opções de "Cadastre-se" e "Login". Após o login, o usuário é redirecionado ao **dashboard principal**.
9. Código do projeto em **inglês**. Aspas SIMPLES sempre que possível. Conformidade com **PEP 8**.
10. Toda informação exibida na interface (UI) DEVE estar em **português brasileiro**.
11. Design: moderno, responsivo, fundo claro, gradientes de cores e paletas harmônicas. Identidade visual e design system ÚNICOS — todas as telas DEVEM compartilhar os mesmos componentes e padrão visual.
12. NÃO implementar Docker inicialmente — alocar em sprints finais.
13. NÃO implementar testes inicialmente — alocar em sprints finais.
</mandatory_technical_constraints>

<anti_overengineering_rules>
- NÃO adicione NADA além do que foi explicitamente solicitado.
- O projeto DEVE ser simples, enxuto e direto. Rejeite abstrações prematuras, camadas desnecessárias, microsserviços, cache distribuído, filas, ou qualquer complexidade não justificada pelos requisitos.
- Na dúvida entre uma solução simples e uma sofisticada, escolha SEMPRE a simples.
</anti_overengineering_rules>

<execution_protocol>
ANTES de escrever o PRD, execute internamente esta auditoria (não exiba o raciocínio, apenas garanta o resultado):
1. Releia `<business_domain>` e mapeie cada entidade para uma app Django.
2. Releia `<mandatory_technical_constraints>` e confirme que cada item será refletido no documento.
3. Verifique que NENHUMA tecnologia proibida foi introduzida.
4. Confirme que a granularidade da lista de tarefas atende ao nível exigido em `<output_format>`.
Só então produza o documento final.
</execution_protocol>

<output_format>
Gere UM arquivo Markdown contendo o PRD com EXATAMENTE a seguinte estrutura enumerada. Use títulos hierárquicos, tabelas quando apropriado e blocos de código `mermaid` onde indicado.

1. **Visão geral**
2. **Sobre o produto**
3. **Propósito**
4. **Público-alvo**
5. **Objetivos**
6. **Requisitos funcionais**
   - 6.x Liste cada requisito funcional por módulo (Cadastros, Movimentação, Financeiro, Consultas, Relatórios, Usuários, Manutenção, Site público).
   - Inclua um **flowchart Mermaid** com os fluxos de UX principais (login → dashboard → operações).
7. **Requisitos não-funcionais** (desempenho, usabilidade, responsividade, segurança, manutenibilidade, i18n da UI em pt-BR).
8. **Arquitetura técnica**
   - 8.1 **Stack** (versões e justificativa enxuta).
   - 8.2 **Estrutura de dados** — diagrama **Mermaid (erDiagram)** com todos os models, campos, tipos e relacionamentos. TODOS os models com `created_at` e `updated_at`.
   - 8.3 **Mapa de apps Django** (qual app contém quais models/responsabilidades).
9. **Design system**
   - Cores primárias e secundárias (com paleta de gradiente e códigos hex), cores de fundo (claro).
   - Padrões de: botões, inputs, forms, grids, menus/navegação, tipografia/fontes.
   - TUDO especificado como classes utilitárias TailwindCSS aplicadas dentro do DTL, com exemplos de marcação.
10. **User stories**
    - Organizadas por **Épico**.
    - Cada story com **critérios de aceite** claros e verificáveis.
11. **Métricas de sucesso** (KPIs de produto, de usuário e operacionais).
12. **Riscos e mitigações** (tabela: risco | impacto | probabilidade | mitigação).
13. **Lista de tarefas**
    - Organizada em **Sprints** sequenciais.
    - Docker e Testes DEVEM aparecer SOMENTE nas sprints finais.
    - Tarefas e subtarefas enumeradas, cada uma com descrição e detalhamento de escopo e implementação.
    - Formato de **checklist** com `- [ ]` para marcação de progresso por tarefa E subtarefa.
    - GRANULARIDADE MÁXIMA: quebre cada tarefa em subtarefas pequenas, específicas e atômicas. Prefira muitas subtarefas detalhadas a poucas genéricas. Cada subtarefa deve ser implementável de forma isolada.
</output_format>

<quality_bar>
- Densidade alta, zero enchimento. Cada frase deve agregar informação.
- Consistência total: o que está na arquitetura DEVE bater com os models, com o design system e com a lista de tarefas.
- Diagramas Mermaid DEVEM ser sintaticamente válidos.
- O documento DEVE ser autossuficiente: um desenvolvedor consegue executar o projeto seguindo apenas este PRD.
</quality_bar>

<final_mandate>
Produza AGORA o PRD completo em um único arquivo Markdown, seguindo `<output_format>` à risca. NÃO peça confirmação, NÃO faça perguntas, NÃO gere código. Respeite TODAS as restrições obrigatórias e as regras anti-over-engineering. A UI em português brasileiro; o documento e o código de exemplo em inglês onde aplicável conforme as regras.
</final_mandate>

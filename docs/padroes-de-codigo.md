# Padrões de código

Regras inegociáveis de idioma, estilo e organização. Derivam dos requisitos não-funcionais do PRD (RNF-05) e dos objetivos do projeto.

## Idioma

- **UI: 100% em português brasileiro.** Todo texto visível ao usuário (rótulos, mensagens, títulos, botões) em pt-BR.
- **Código: 100% em inglês.** Identificadores (variáveis, funções, classes, modelos, campos) e comentários em inglês.

## Estilo

- Aderência a **PEP 8**.
- **Aspas simples** para strings.
- `LANGUAGE_CODE = 'pt-br'` e `TIME_ZONE = 'America/Sao_Paulo'` no settings.

## Organização

- **Uma app Django por domínio de negócio** (ver [arquitetura](arquitetura.md)). Responsabilidades segregadas.
- **Class-Based Views (CBVs)** e recursos nativos do Django sempre que possível. Sem abstração prematura.
- **Signals em `signals.py`** da app correspondente, apenas quando necessário.
- Regras de negócio sensíveis (cálculo de juros/multa, numeração de locação) centralizadas em um único ponto — nunca duplicadas.

## Simplicidade (anti-over-engineering)

- Construir **apenas o que o requisito pede**. Nada além.
- Sem SPA, sem framework CSS além do Tailwind, sem dependências não justificadas.
- **Docker e testes automatizados são adiados para as sprints finais** (12 e 13). Não adicioná-los antes, salvo solicitação explícita.

## Segurança

- Autenticação nativa do Django, com custom user por e-mail (sem username).
- Proteção CSRF do Django ativa.
- Senhas com o hashing padrão do Django.
- Controle de acesso por módulo aplicado via mixin único nas views.

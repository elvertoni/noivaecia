# Roadmap de sprints

Ordem de entrega planejada. A lista de tarefas detalhada e o status (`- [ ]` / `- [x]`) ficam no [`PRD.md`](../PRD.md) §13 — atualize lá conforme avança.

| Sprint | Tema | Apps / entregas principais |
|---|---|---|
| 0 | Fundação do projeto | Projeto Django, TailwindCSS, app `core`, `TimeStampedModel`, `base.html` e parciais |
| 1 | Autenticação e usuários | `accounts`: custom user por e-mail, signup/login/logout, `ModulePermission` + mixin |
| 2 | Site público e dashboard | `website`, dashboard em `core` com atalhos e indicadores |
| 3 | Cadastro de clientes | `customers`: model `Customer` + CRUD CBV |
| 4 | Catálogo | `catalog`: `Category` e `Product` + CRUD; filtro por prefixo |
| 5 | Empresa / configuração | `company`: singleton `Company`; helper de próximo número de locação |
| 6 | Locações e itens | `rentals`: `Rental`, `RentalItem`; numeração sequencial; formset de itens |
| 7 | Movimentação | `movements`: `Pickup`, `Return`; serviço de dias de atraso e multa |
| 8 | Financeiro | `billing`: `Receivable`; serviço único de juros por atraso; pagamentos |
| 9 | Consultas e relatórios | Disponibilidade em `catalog`; `reports` com tipos e filtros |
| 10 | Administração e manutenção | Permissões por módulo aplicadas; `maintenance` restrita |
| 11 | Refino de UI/UX | Auditoria contra o design system, feedback pt-BR, responsividade |
| 12 (final) | Testes | Runner Django; testes de models, auth, locação, juros, permissões |
| 13 (final) | Docker e deploy | `Dockerfile`, `docker-compose.yml`, estáticos, `DEBUG=False` |

> Docker e testes automatizados aparecem apenas nas sprints finais (12 e 13), mantendo o MVP enxuto.

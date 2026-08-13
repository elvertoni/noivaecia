# Product

## Register

product

## Users

Noivas & Cia é usado principalmente pela equipe interna da locadora, em um balcão de atendimento e em rotinas administrativas:

- Atendentes criam locações, conferem clientes e peças, registram retiradas, devoluções e recebimentos. Precisam de telas rápidas, previsíveis e com pouca digitação.
- Operadores financeiros acompanham títulos, registram valores pagos de forma livre, conferem caixa e investigam divergências.
- Administradores gerenciam acervo, empresa, usuários, permissões, manutenção e relatórios.
- Gestores precisam enxergar o estado operacional e financeiro sem confundir histórico legado com atividade atual.
- Visitantes usam o site público para conhecer a empresa e iniciar cadastro ou login.

## Product Purpose

O produto substitui a operação dependente de um sistema Access/Windows legado por uma aplicação web centralizada para aluguel de roupas e acessórios de festa, noivas, formaturas e trajes masculinos.

Ele cobre o ciclo completo: cadastro de clientes e acervo, contrato de locação, itens e disponibilidade, retirada, devolução, penalidades, recebimentos, relatórios, usuários e permissões. A interface interna é server-side, em português brasileiro, e deve funcionar bem no atendimento presencial, onde datas, peças, valores e status precisam ser confirmados rapidamente.

O produto deve preservar o histórico importado sem deixar que suas limitações ditem a operação nova. Para locações novas, deve existir uma entrada no ato, a dívida deve estar quitada até a retirada e os valores intermediários podem ser amortizados livremente. O sucesso é reduzir erro de balcão, conflitos de disponibilidade, ambiguidade financeira e dependência de conhecimento tribal, mantendo rastreabilidade para auditoria.

## Brand Personality

Confiável, claro e acolhedor.

A voz da aplicação é direta, respeitosa e operacional. Mensagens devem explicar o que aconteceu, qual regra foi aplicada e qual ação o usuário pode tomar. O produto não usa jargão financeiro desnecessário nem trata alertas de legado como falhas do operador.

## Anti-references

- Não parecer um painel SaaS genérico cheio de métricas decorativas, gradientes ou animações sem função.
- Não reproduzir a ambiguidade visual do sistema Access: campos sem contexto, estados implícitos, datas pouco legíveis ou valores sem composição clara.
- Não esconder bloqueios financeiros, conflitos de disponibilidade ou permissões atrás de mudanças silenciosas.
- Não misturar histórico legado com operação nova sem indicar a origem e a política aplicável.
- Não transformar o fluxo de balcão em um formulário longo, ornamental ou dependente de navegação por mouse.
- Não usar a página pública como referência visual para as telas densas de operação interna.

## Design Principles

1. **Operação de balcão primeiro.** A ação principal deve ser evidente, rápida e compatível com teclado, toque e uso em telas menores.
2. **Estado explícito.** Datas, valores, saldo, disponibilidade, status, origem legada e permissões devem aparecer no contexto da decisão, sem exigir interpretação.
3. **Regra com caminho de recuperação.** Bloqueios protegem o negócio, mas mensagens e fluxos devem mostrar como corrigir a situação. O legado recebe tratamento compatível e identificável.
4. **Dinheiro e peças têm trilha.** Toda mudança relevante precisa preservar histórico, vínculo, operador e origem para conferência posterior.
5. **Densidade a serviço da tarefa.** Tabelas e grades podem ser densas quando ajudam a conferir dados, mas a hierarquia, o foco e a responsividade não podem ser sacrificados.

## Accessibility & Inclusion

O produto deve perseguir WCAG 2.1 AA nas telas internas e públicas. Isso inclui navegação por teclado, foco visível, alvos de toque adequados, rótulos e mensagens associados aos campos, contraste suficiente, uso de texto além de cor, suporte a zoom e comportamento equivalente em desktop, tablet e celular.

Movimento deve comunicar estado e respeitar `prefers-reduced-motion`. Mensagens operacionais devem estar em português brasileiro, evitar abreviações ambíguas e explicar erros de validação junto ao campo afetado. Informações financeiras e de disponibilidade não podem depender apenas de cor, ícone ou posição.

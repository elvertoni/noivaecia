# Visão geral

## O produto

Noivas & Cia é um sistema web de gerenciamento comercial e locação para uma empresa de aluguel de roupas e acessórios para grandes eventos (noivas, festas, formaturas, trajes masculinos). Substitui um sistema legado desktop que roda apenas em Windows 98.

O sistema cobre o ciclo completo de operação: cadastro de clientes e itens do acervo, criação de contratos de locação, controle de retirada e devolução, gestão financeira de recebimentos com juros por atraso, consulta de disponibilidade, relatórios de acompanhamento e administração de usuários.

## Tipo de aplicação

| Característica | Definição |
|---|---|
| Tipo | Aplicação web monolítica server-side |
| Acesso | Interno (operação) + público (apresentação) |
| Renderização | Server-side (Django Template Language) |
| Estilo | TailwindCSS |
| Persistência | SQLite |
| Idioma da UI | Português brasileiro |
| Idioma do código | Inglês |

## Personas

| Persona | Descrição |
|---|---|
| Atendente | Opera locações, retiradas, devoluções e recebimentos no balcão |
| Administrador | Gerencia acervo, configurações da empresa, usuários e permissões |
| Visitante | Conhece a empresa pelo site público e cria conta / faz login |

## Stack

| Camada | Tecnologia | Versão alvo |
|---|---|---|
| Linguagem | Python | 3.12+ |
| Framework | Django | 5.x |
| Templates | Django Template Language | — |
| Estilo | TailwindCSS | 3.x |
| Banco | SQLite | nativo |
| Autenticação | `django.contrib.auth` (custom user por e-mail) | nativo |

## Princípios

- Full-stack monolítico: backend e frontend no mesmo projeto Django.
- Sem SPA, sem framework CSS além do Tailwind, sem serviços externos.
- Recursos nativos do Django sempre que possível; nenhuma abstração prematura.
- Sistema deliberadamente enxuto — nada além do que os requisitos pedem.

# Arquitetura

Cada domínio de negócio é isolado em uma app Django, mantendo responsabilidades segregadas.

## Mapa de apps

| App | Responsabilidade | Models |
|---|---|---|
| `core` | Base abstrata, dashboard, mixins, templates de layout | `TimeStampedModel` (abstrato) |
| `accounts` | Usuário customizado (login por e-mail), signup, login/logout, permissões por módulo | `User`, `ModulePermission` |
| `website` | Site público de apresentação, entrada para signup/login | — |
| `customers` | Cadastro de clientes | `Customer` |
| `catalog` | Categorias, produtos e consulta de disponibilidade | `Category`, `Product` |
| `company` | Configuração singleton da empresa | `Company` |
| `rentals` | Locações e itens da locação | `Rental`, `RentalItem` |
| `movements` | Retiradas e devoluções | `Pickup`, `Return` |
| `billing` | Recebimentos, parcelas e juros por atraso | `Receivable` |
| `reports` | Relatórios de acompanhamento (sem models próprios) | — |
| `maintenance` | Rotinas administrativas controladas | — |

> `Category` e `Product` coabitam a app `catalog` por pertencerem ao mesmo domínio. A consulta de disponibilidade é uma view em `catalog`, não uma app dedicada.

## Convenções estruturais

Estas convenções atravessam várias apps e devem ser seguidas sempre:

- **Todos os models herdam de `core.TimeStampedModel`** (abstrato), que fornece `created_at` (`auto_now_add`) e `updated_at` (`auto_now`) em todas as tabelas.
- **Class-Based Views (CBVs) em todo lugar.** Usar as views genéricas do Django (`ListView`, `CreateView`, `UpdateView`, `DeleteView`, `DetailView`) e recursos nativos antes de escrever código próprio.
- **Controle de acesso por módulo é um único mixin reutilizável** (em `core`), aplicado a toda view de módulo. A permissão por usuário fica em `accounts.ModulePermission` (`user`, `module_key`, `allowed`). Não reimplementar a checagem por view.
- **Cálculo de juros/multa fica em um único serviço**, não espalhado. Juros de atraso = taxa diária (`Company.daily_interest_rate`) × dias de atraso. Centralizar a regra evita inconsistência.
- **Numeração de locação** é sequencial, baseada em `Company.last_rental_number` (singleton). Gerar o próximo número por um helper em `company`; não duplicar a lógica de incremento.
- **`Company` é singleton** — garantir uma única linha.
- **Signals ficam no `signals.py` da app correspondente** (ex.: sincronizar `Rental.status` na retirada/devolução). Adicionar apenas quando realmente necessário.

## Modelo de dados

Diagrama de entidades e relacionamentos (`erDiagram`):

```mermaid
erDiagram
    USER {
        int id PK
        string email UK
        string first_name
        string last_name
        bool is_active
        bool is_staff
        datetime created_at
        datetime updated_at
    }

    COMPANY {
        int id PK
        string name
        string address
        string city
        string cnpj
        string phones
        int last_rental_number
        decimal daily_interest_rate
        string footer_message
        datetime created_at
        datetime updated_at
    }

    CUSTOMER {
        int id PK
        string name
        string address
        string district
        string city
        string rg
        string cpf
        string phone_home
        string phone_mobile
        string phone_work
        text notes
        datetime created_at
        datetime updated_at
    }

    CATEGORY {
        int id PK
        string prefix UK
        string name
        datetime created_at
        datetime updated_at
    }

    PRODUCT {
        int id PK
        int category_id FK
        int code
        string description
        string color
        string size
        decimal value
        text notes
        datetime created_at
        datetime updated_at
    }

    RENTAL {
        int id PK
        int number UK
        int customer_id FK
        date pickup_date
        date return_date
        decimal total_value
        decimal penalty_value
        text notes
        string status
        datetime created_at
        datetime updated_at
    }

    RENTAL_ITEM {
        int id PK
        int rental_id FK
        int product_id FK
        string description
        decimal value
        datetime created_at
        datetime updated_at
    }

    PICKUP {
        int id PK
        int rental_id FK
        date pickup_date
        datetime created_at
        datetime updated_at
    }

    RETURN {
        int id PK
        int rental_id FK
        date return_date
        int days_late
        decimal penalty_applied
        datetime created_at
        datetime updated_at
    }

    RECEIVABLE {
        int id PK
        int rental_id FK
        date due_date
        decimal amount
        decimal paid_amount
        decimal balance
        date last_payment_date
        datetime created_at
        datetime updated_at
    }

    MODULE_PERMISSION {
        int id PK
        int user_id FK
        string module_key
        bool allowed
        datetime created_at
        datetime updated_at
    }

    CUSTOMER ||--o{ RENTAL : "possui"
    CATEGORY ||--o{ PRODUCT : "agrupa"
    RENTAL ||--o{ RENTAL_ITEM : "contém"
    PRODUCT ||--o{ RENTAL_ITEM : "é locado em"
    RENTAL ||--o| PICKUP : "tem retirada"
    RENTAL ||--o| RETURN : "tem devolução"
    RENTAL ||--o{ RECEIVABLE : "gera parcelas"
    USER ||--o{ MODULE_PERMISSION : "tem acesso"
```

# PRD_DB — Migração de Persistência e Otimização de Banco

> Documento de Requisitos de Produto/Técnico · v1.0  
> Data: 2026-06-16  
> Produto: Noivas & Cia  
> Escopo: migração de SQLite para PostgreSQL, revisão de storage de arquivos, busca indexada, backups e operação em VPS Ubuntu Server.

---

## 1. Resumo executivo

O sistema Noivas & Cia ainda está em fase de teste. Isso permite executar uma mudança estrutural de banco com menor custo operacional: substituir SQLite por PostgreSQL como banco padrão de produção, remover armazenamento de fotos em BLOB dentro do banco e preparar a aplicação para consultas mais rápidas, backups confiáveis e maior concorrência na VPS Ubuntu Server.

A decisão proposta é:

- **Produção e homologação**: PostgreSQL 16.
- **Desenvolvimento local**: PostgreSQL preferencial via Docker Compose; SQLite permitido apenas como fallback explícito para desenvolvimento rápido.
- **Arquivos enviados pelo usuário**: `FileField` em volume de mídia, não `BinaryField` no banco.
- **Busca textual e por documentos**: campos normalizados persistidos + índices apropriados; trigram PostgreSQL para busca parcial quando necessário.
- **Backups**: `pg_dump` + backup do volume de mídia + teste de restauração documentado.

Como o sistema está em teste, o caminho recomendado é **migração limpa**:

1. congelar schema atual;
2. aplicar novas migrations estruturais;
3. recriar banco PostgreSQL;
4. reimportar dados do legado ou carregar fixtures limpas;
5. validar contagens, fluxos críticos e relatórios;
6. descartar o SQLite como banco de produção.

---

## 2. Contexto atual

### 2.1 Estado técnico

O sistema é um monólito Django 5 com apps por domínio:

- `accounts`
- `billing`
- `catalog`
- `company`
- `core`
- `customers`
- `maintenance`
- `movements`
- `rentals`
- `reports`
- `website`

Hoje a persistência principal está configurada em `noivas_cia/settings.py` como SQLite:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('DATABASE_NAME', BASE_DIR / 'db.sqlite3'),
        'OPTIONS': {'timeout': 20},
    }
}
```

O Docker Compose atual persiste o SQLite em volume:

```yaml
volumes:
  - sqlite_data:/app/data
```

### 2.2 Problemas motivadores

SQLite funcionou para MVP e homologação inicial, mas traz limitações importantes para produção em VPS:

| Problema | Impacto |
|---|---|
| Escrita concorrente limitada | Risco de `database is locked` com múltiplos workers Gunicorn |
| Backups de arquivo único | Exige cuidado extra para consistência |
| Relatórios longos | Leituras pesadas podem disputar com escritas |
| Busca com `icontains` | Tende a table scan conforme clientes/produtos crescem |
| Fotos em `BinaryField` | Banco cresce rapidamente, backup fica pesado, consultas podem carregar BLOB sem necessidade |
| Falta de recursos avançados | Sem trigram, índices parciais robustos, locks mais previsíveis e ferramentas maduras de operação |

### 2.3 Volume local observado

Na auditoria local, a base SQLite já apresentou volume não trivial:

| Entidade | Volume aproximado |
|---|---:|
| Clientes | 18.784 |
| Produtos | 10.271 |
| Locações | 35.652 |
| Itens de locação | 71.099 |
| Recebíveis | 54.900 |
| Movimentos financeiros | 33.758 |

Esse volume ainda é pequeno para PostgreSQL, mas já é suficiente para justificar índices corretos, busca planejada e separação de arquivos do banco.

---

## 3. Objetivos

### 3.1 Objetivos principais

1. Migrar a persistência de produção para PostgreSQL.
2. Reduzir risco de travamento por concorrência.
3. Remover fotos do banco e armazená-las como arquivos gerenciados.
4. Criar base técnica para busca rápida por cliente, CPF, RG, telefone, produto, prefixo e código.
5. Padronizar backups e restauração em ambiente VPS.
6. Manter o deploy simples via Docker Compose.
7. Garantir que todos os testes automatizados passem após a migração.

### 3.2 Objetivos secundários

1. Preparar o sistema para crescimento de dados sem reescrita de views.
2. Diminuir memória usada em relatórios.
3. Melhorar auditoria operacional de pagamentos, estornos e movimentos financeiros.
4. Documentar operação mínima de banco para manutenção da VPS.

---

## 4. Não objetivos

Esta migração **não** deve:

- Transformar o sistema em microserviços.
- Trocar Django ORM por SQL manual amplo.
- Introduzir fila, Redis, Celery ou cache distribuído sem necessidade imediata.
- Migrar para Kubernetes.
- Adotar storage externo como S3 obrigatoriamente nesta fase.
- Reescrever todos os relatórios de uma vez se os limites e índices já resolverem o gargalo imediato.

---

## 5. Decisão técnica proposta

### 5.1 Banco padrão

Usar PostgreSQL 16 em produção/homologação.

Configuração desejada:

| Item | Valor |
|---|---|
| Engine Django | `django.db.backends.postgresql` |
| Driver | `psycopg` |
| Versão Postgres | 16 |
| Charset | UTF-8 |
| Time zone | `America/Sao_Paulo` na aplicação; UTC interno aceitável no banco |
| Conexão | `DATABASE_URL` ou variáveis explícitas |
| Pooling | não obrigatório inicialmente |

### 5.2 Arquivos e mídia

Substituir `RentalItem.proof_photo` de `BinaryField` por `FileField`.

Configuração desejada:

| Item | Valor |
|---|---|
| Campo | `proof_photo_file` ou substituição direta por `proof_photo` como `FileField` |
| Storage inicial | filesystem local |
| Volume Docker | `/app/media` |
| URL | `MEDIA_URL=/media/` |
| Root | `MEDIA_ROOT=/app/media` em Docker |
| Upload path | `rentals/proof_photos/%Y/%m/` |
| Acesso | view autenticada, não URL pública direta para fotos sensíveis |

Como o sistema está em teste, recomenda-se substituição limpa:

- criar novo campo `proof_photo_file`;
- ajustar forms/views/templates para usar arquivo;
- manter metadados (`content_type`, `filename`, `size`, `width`, `height`);
- remover `BinaryField` em migration posterior depois da validação;
- se houver fotos de teste importantes, criar comando opcional para exportar BLOBs antes da remoção.

### 5.3 Busca

Usar duas camadas:

1. **Campos normalizados persistidos** para documentos/telefones.
2. **Índices trigram** para busca textual parcial em PostgreSQL.

Campos normalizados recomendados:

| Model | Campo | Fonte |
|---|---|---|
| `Customer` | `cpf_digits` | `cpf` sem pontuação |
| `Customer` | `rg_digits` | `rg` sem pontuação |
| `Customer` | `phone_home_digits` | `phone_home` sem pontuação |
| `Customer` | `phone_mobile_digits` | `phone_mobile` sem pontuação |
| `Customer` | `phone_work_digits` | `phone_work` sem pontuação |
| `Customer` | `name_search` | nome normalizado, minúsculo, sem espaços duplicados |
| `Product` | `description_search` | descrição normalizada |

Índices recomendados:

| Model | Índice |
|---|---|
| `Customer` | B-tree em `cpf_digits` |
| `Customer` | B-tree em `rg_digits` |
| `Customer` | B-tree em `phone_mobile_digits` |
| `Customer` | GIN trigram em `name` ou `name_search` |
| `Product` | B-tree em `(category_id, code)` |
| `Product` | GIN trigram em `description` ou `description_search` |
| `Rental` | `(status, pickup_date, number)` |
| `Rental` | `(status, return_date, number)` |
| `Receivable` | índice parcial em `due_date WHERE balance > 0` |
| `FinancialMovement` | `(-date, -created_at)` |
| `FinancialMovement` | `(source, direction, date)` |

---

## 6. Requisitos funcionais

### DB-01 — Configuração de banco por ambiente

O sistema deve permitir configurar o banco por variável de ambiente.

Critérios:

- `DATABASE_URL` deve ser aceito quando presente.
- Em produção (`DJANGO_DEBUG=False`), ausência de `DATABASE_URL` deve falhar explicitamente, salvo se uma variável `ALLOW_SQLITE_IN_PRODUCTION=True` for definida para contingência.
- SQLite pode continuar disponível para desenvolvimento local apenas.
- A documentação deve explicar os dois modos.

Exemplo esperado:

```env
DATABASE_URL=postgresql://noivas:senha@db:5432/noivas_cia
```

### DB-02 — Docker Compose com PostgreSQL

O Compose deve subir app + PostgreSQL.

Critérios:

- Serviço `db` com imagem `postgres:16-alpine`.
- Volume `postgres_data`.
- Healthcheck do Postgres.
- App depende de `db` saudável.
- Variáveis sensíveis ficam em `.env`.
- App não expõe banco publicamente.

Exemplo alvo:

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-noivas_cia}
      POSTGRES_USER: ${POSTGRES_USER:-noivas}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    depends_on:
      db:
        condition: service_healthy
```

### DB-03 — Migrations limpas para PostgreSQL

O sistema deve ter migrations compatíveis com PostgreSQL.

Critérios:

- `python manage.py migrate` deve rodar do zero em PostgreSQL.
- `python manage.py makemigrations --check --dry-run` deve retornar `No changes detected`.
- Nenhuma migration deve depender de comportamento específico do SQLite.
- Índices parciais e trigram devem ter migrations explícitas.

### DB-04 — Migração/reset de dados de teste

Como o sistema ainda está em teste, o caminho padrão será reset limpo.

Critérios:

- Documentar comando para derrubar volumes de homologação.
- Documentar recriação do banco.
- Documentar reimportação de dados legados.
- Documentar validação por contagens.
- Manter caminho alternativo via `dumpdata/loaddata` apenas se houver dados de teste a preservar.

Caminho recomendado:

```bash
docker compose down
docker volume rm noivas-cia_postgres_data
docker compose up -d db
docker compose run --rm app python manage.py migrate
docker compose run --rm app python manage.py import_legacy_access --input-dir /app/data/legacy_export
docker compose run --rm app python manage.py test
```

### DB-05 — Fotos fora do banco

O sistema deve armazenar fotos de comprovação em filesystem.

Critérios:

- `RentalItem` deve deixar de usar `BinaryField` como armazenamento principal.
- Upload deve continuar aceitando JPG, PNG e WebP.
- A imagem deve continuar sendo convertida/comprimida.
- Metadados devem continuar persistidos.
- Views de detalhe/contrato/relatórios não devem carregar bytes da foto desnecessariamente.
- A URL de foto deve exigir usuário autenticado com acesso ao módulo de locações.

### DB-06 — Busca de cliente por RG/CPF/telefone sem table scan

O sistema deve buscar clientes por documentos normalizados.

Critérios:

- Buscar `12.345.678-9`, `123456789` ou parte relevante deve funcionar.
- Busca por RG deve funcionar na lista e no autocomplete.
- Busca por CPF deve funcionar com e sem pontuação.
- Busca por celular deve ignorar parênteses, espaços e hífen.
- Campos normalizados devem ser atualizados em `save()`.
- Deve existir data migration para preencher registros existentes.

### DB-07 — Busca textual otimizada

O sistema deve usar índice adequado para busca parcial de nomes e descrições.

Critérios:

- Em PostgreSQL, habilitar extensão `pg_trgm`.
- Criar índice GIN trigram para `Customer.name` ou `Customer.name_search`.
- Criar índice GIN trigram para `Product.description` ou `Product.description_search`.
- Views devem continuar usando ORM.
- Testes devem validar resultado funcional, não plano SQL.

### DB-08 — Numeração de locação segura em concorrência

A geração de número de locação deve ser segura com múltiplos workers.

Critérios:

- `Company.next_rental_number()` deve usar `transaction.atomic()`.
- A linha `Company` deve ser bloqueada com `select_for_update()`.
- Duas locações simultâneas não podem receber o mesmo número.
- Teste unitário ou de integração deve cobrir chamadas sequenciais dentro de transação.

### DB-09 — Pagamentos e estornos transacionais

O fluxo financeiro deve permanecer atômico no PostgreSQL.

Critérios:

- Registro de pagamento cria `Payment`, atualiza `Receivable` e cria `FinancialMovement` na mesma transação.
- Estorno cria pagamento negativo, vincula `reversed_by`, recalcula recebível e cria movimento de saída na mesma transação.
- Falha no movimento financeiro deve reverter o pagamento.
- `FinancialMovement.payment` deve ser preenchido para pagamentos e estornos.

### DB-10 — Backups PostgreSQL

O sistema deve ter rotina clara de backup e restauração.

Critérios:

- Backup do banco via `pg_dump`.
- Backup do volume de mídia.
- Manifesto com data, hash e contagens principais.
- Comando de restore documentado.
- Teste manual de restauração antes do go-live.

Formato esperado do manifesto:

```json
{
  "backup_at": "2026-06-16T12:00:00-03:00",
  "database": "postgresql",
  "database_backup": "/backups/noivas-2026-06-16.dump",
  "database_sha256": "...",
  "media_backup": "/backups/media-2026-06-16.tar.gz",
  "media_sha256": "...",
  "counts": {
    "customers": 18784,
    "products": 10271,
    "rentals": 35652,
    "rental_items": 71099,
    "receivables": 54900,
    "payments": 0,
    "financial_movements": 33758
  }
}
```

### DB-11 — Observabilidade mínima do banco

O deploy deve permitir diagnóstico básico.

Critérios:

- Healthcheck do app deve continuar em `/healthz/`.
- Healthcheck do Postgres deve usar `pg_isready`.
- README deve listar comandos para:
  - ver conexões;
  - ver tamanho do banco;
  - rodar backup;
  - restaurar backup;
  - aplicar migrations;
  - checar logs.

### DB-12 — Compatibilidade dos testes

Todos os testes devem rodar em PostgreSQL.

Critérios:

- Suíte completa `python manage.py test` deve passar.
- Testes não devem depender de ordenação implícita.
- Testes com datas/decimais devem ser independentes do backend.
- Pipeline local deve ter caminho documentado para testar contra Postgres.

---

## 7. Requisitos não funcionais

| ID | Categoria | Requisito |
|---|---|---|
| DB-RNF-01 | Desempenho | Listas principais devem responder em até 1s com a base atual importada |
| DB-RNF-02 | Concorrência | Sistema deve operar com pelo menos 3 workers Gunicorn sem lock global de banco |
| DB-RNF-03 | Backup | Deve ser possível restaurar banco + mídia em ambiente limpo |
| DB-RNF-04 | Integridade | Pagamentos, estornos, locações e movimentações devem ser transacionais |
| DB-RNF-05 | Segurança | Banco não deve ser exposto publicamente na VPS |
| DB-RNF-06 | Manutenibilidade | Configuração de banco deve ficar em env vars e README, não hardcoded |
| DB-RNF-07 | Portabilidade | Desenvolvimento local deve funcionar com Docker Compose |
| DB-RNF-08 | Auditabilidade | Migrations devem ser pequenas, nomeadas e reversíveis quando possível |

---

## 8. Modelo de dados alvo

### 8.1 `customers.Customer`

Adicionar:

```python
cpf_digits = models.CharField(max_length=14, blank=True, db_index=True)
rg_digits = models.CharField(max_length=20, blank=True, db_index=True)
phone_home_digits = models.CharField(max_length=20, blank=True, db_index=True)
phone_mobile_digits = models.CharField(max_length=20, blank=True, db_index=True)
phone_work_digits = models.CharField(max_length=20, blank=True, db_index=True)
name_search = models.CharField(max_length=180, blank=True)
```

Regra:

- Atualizar campos no `save()`.
- Usar helper puro, testável, sem dependência de request.
- Data migration deve preencher todos os registros existentes.

Índices:

```python
models.Index(fields=('name',), name='customer_name_idx')
models.Index(fields=('cpf_digits',), name='customer_cpf_digits_idx')
models.Index(fields=('rg_digits',), name='customer_rg_digits_idx')
models.Index(fields=('phone_mobile_digits',), name='customer_mobile_digits_idx')
```

PostgreSQL:

```python
GinIndex(
    fields=['name_search'],
    name='customer_name_trgm_idx',
    opclasses=['gin_trgm_ops'],
)
```

### 8.2 `catalog.Product`

Adicionar:

```python
description_search = models.CharField(max_length=220, blank=True)
```

Restrições desejadas:

Se os dados legados estiverem limpos, adicionar:

```python
models.UniqueConstraint(
    fields=('category', 'code'),
    name='catalog_product_category_code_uniq',
)
```

Se ainda houver duplicados reais vindos do legado:

- manter sem unique temporariamente;
- criar relatório de duplicados;
- resolver duplicados;
- adicionar constraint em migration posterior.

Índices:

```python
models.Index(fields=('category', 'code'), name='catalog_product_lookup_idx')
```

PostgreSQL:

```python
GinIndex(
    fields=['description_search'],
    name='product_desc_trgm_idx',
    opclasses=['gin_trgm_ops'],
)
```

### 8.3 `rentals.RentalItem`

Modelo alvo:

```python
proof_photo = models.FileField(
    'foto de comprovação',
    upload_to='rentals/proof_photos/%Y/%m/',
    blank=True,
)
proof_photo_content_type = models.CharField(max_length=50, blank=True)
proof_photo_filename = models.CharField(max_length=150, blank=True)
proof_photo_size = models.PositiveIntegerField(default=0)
proof_photo_width = models.PositiveIntegerField(default=0)
proof_photo_height = models.PositiveIntegerField(default=0)
```

Observação:

- Se o nome `proof_photo` já existe como `BinaryField`, usar migração em duas fases:
  1. adicionar `proof_photo_file`;
  2. atualizar código;
  3. migrar/exportar dados se necessário;
  4. remover `proof_photo`;
  5. renomear `proof_photo_file` para `proof_photo`.

Como o sistema está em teste, a estratégia pode ser simplificada:

- remover BLOB;
- recriar banco;
- reimportar dados;
- fotos de teste podem ser descartadas.

### 8.4 `billing.FinancialMovement`

Manter/adicionar:

```python
payment = models.ForeignKey(
    Payment,
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name='financial_movements',
)
```

Índices:

```python
models.Index(fields=('-date', '-created_at'), name='fmv_date_created_idx')
models.Index(fields=('direction', 'date'), name='fmv_direction_date_idx')
models.Index(fields=('source', 'direction', 'date'), name='fmv_source_direction_date_idx')
```

### 8.5 `billing.Receivable`

Índices:

```python
models.Index(fields=('due_date',), name='rcv_due_date_idx')
models.Index(fields=('balance',), name='rcv_balance_idx')
models.Index(fields=('due_date', 'balance'), name='rcv_overdue_idx')
models.Index(fields=('balance', 'due_date'), name='rcv_balance_due_idx')
models.Index(fields=('rental', 'due_date'), name='rcv_rental_due_idx')
models.Index(
    fields=('due_date',),
    condition=Q(balance__gt=0),
    name='rcv_open_due_idx',
)
```

### 8.6 `rentals.Rental`

Índices:

```python
models.Index(fields=('customer', 'status'), name='rental_customer_status_idx')
models.Index(fields=('status', 'pickup_date', 'number'), name='rental_status_pickup_num_idx')
models.Index(fields=('status', 'return_date', 'number'), name='rental_status_return_num_idx')
models.Index(fields=('customer', 'pickup_date'), name='rental_customer_pickup_idx')
```

---

## 9. Configuração alvo

### 9.1 Dependências Python

Adicionar a `requirements.txt`:

```txt
psycopg[binary]>=3.2,<4
dj-database-url>=2.2,<3
```

`dj-database-url` é opcional, mas recomendado para reduzir parsing manual de URL.

### 9.2 Variáveis de ambiente

Atualizar `.env.example`:

```env
DJANGO_ENV=production
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=troque-por-chave-forte
DJANGO_ALLOWED_HOSTS=app.example.com,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://app.example.com

DATABASE_URL=postgresql://noivas:${POSTGRES_PASSWORD}@db:5432/noivas_cia
POSTGRES_DB=noivas_cia
POSTGRES_USER=noivas
POSTGRES_PASSWORD=troque-esta-senha

MEDIA_ROOT=/app/media
MEDIA_URL=/media/
BACKUP_ROOT=/app/data/backups

SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
DJANGO_SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
```

### 9.3 Settings Django

Comportamento esperado:

```python
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=60,
            conn_health_checks=True,
        )
    }
elif DEBUG:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.environ.get('DATABASE_NAME', BASE_DIR / 'db.sqlite3'),
            'OPTIONS': {'timeout': 20},
        }
    }
else:
    raise ImproperlyConfigured('DATABASE_URL is required when DJANGO_DEBUG is false.')
```

Configurar mídia:

```python
MEDIA_URL = os.environ.get('MEDIA_URL', '/media/')
MEDIA_ROOT = Path(os.environ.get('MEDIA_ROOT', BASE_DIR / 'media'))
```

---

## 10. Plano de implementação

### Fase 0 — Preparação

Objetivo: garantir ponto de partida limpo.

Tarefas:

- [ ] Rodar `python manage.py test`.
- [ ] Rodar `python manage.py makemigrations --check --dry-run`.
- [ ] Rodar `python manage.py check --deploy` com env de produção simulado.
- [ ] Gerar backup do SQLite atual, mesmo sendo teste.
- [ ] Registrar contagens atuais em manifesto.

Critério de saída:

- Testes passando.
- Backup local criado.
- Lista de dados que podem ser descartados confirmada.

### Fase 1 — Dependências e settings

Tarefas:

- [ ] Adicionar `psycopg[binary]`.
- [ ] Adicionar `dj-database-url` ou parser próprio.
- [ ] Implementar `DATABASE_URL`.
- [ ] Configurar `MEDIA_ROOT` e `MEDIA_URL`.
- [ ] Atualizar `.env.example`.
- [ ] Atualizar README.

Critério de saída:

- `python manage.py check` passa com SQLite local.
- `python manage.py check` passa com `DATABASE_URL` PostgreSQL.

### Fase 2 — Docker Compose PostgreSQL

Tarefas:

- [ ] Adicionar serviço `db`.
- [ ] Adicionar volume `postgres_data`.
- [ ] Adicionar healthcheck `pg_isready`.
- [ ] Ajustar `depends_on`.
- [ ] Adicionar volume `media_data:/app/media`.
- [ ] Garantir que porta 5432 não é publicada no host.

Critério de saída:

- `docker compose up -d db` sobe saudável.
- `docker compose up --build app` aplica migrations.
- `/healthz/` responde no app.

### Fase 3 — Migrations de busca e índices

Tarefas:

- [ ] Adicionar campos normalizados em `Customer`.
- [ ] Adicionar `description_search` em `Product`.
- [ ] Implementar normalização em helpers.
- [ ] Criar data migration para preencher campos.
- [ ] Criar índices B-tree.
- [ ] Criar migration para `pg_trgm`.
- [ ] Criar índices GIN trigram condicionados ao PostgreSQL, se necessário.

Critério de saída:

- Busca por RG/CPF/telefone funciona sem anotação `Replace()` em runtime.
- `makemigrations --check --dry-run` sem mudanças pendentes.
- Testes de busca passam.

### Fase 4 — Fotos fora do banco

Tarefas:

- [ ] Definir estratégia final: reset limpo ou migração em duas fases.
- [ ] Alterar `RentalItem` para `FileField`.
- [ ] Ajustar `process_proof_photo()` para retornar `ContentFile` ou arquivo salvo.
- [ ] Ajustar forms para salvar arquivo.
- [ ] Ajustar view de foto para ler do storage.
- [ ] Ajustar templates que exibem foto.
- [ ] Remover `defer('proof_photo')` onde o campo deixar de ser BLOB; manter `select_related`/`prefetch` enxutos.
- [ ] Adicionar volume `media_data`.

Critério de saída:

- Upload de foto funciona.
- Foto aparece na rota protegida.
- Relatórios não carregam arquivo.
- Backup de mídia documentado.

### Fase 5 — Concorrência e transações

Tarefas:

- [ ] Revisar `Company.next_rental_number()` com `select_for_update()`.
- [ ] Validar `register_payment()` e `reverse_payment()`.
- [ ] Validar criação de locação com entrada em uma transação.
- [ ] Validar geração de parcelas.

Critério de saída:

- Pagamento/estorno continuam atômicos.
- Número de locação não duplica.

### Fase 6 — Reset/reimportação

Tarefas:

- [ ] Derrubar volumes de teste.
- [ ] Subir Postgres limpo.
- [ ] Aplicar migrations.
- [ ] Criar superusuário.
- [ ] Reimportar legado.
- [ ] Rodar relatório de qualidade de importação.
- [ ] Comparar contagens com manifesto esperado.

Critério de saída:

- Base PostgreSQL populada.
- Admin acessa o sistema.
- Fluxos críticos funcionam.

### Fase 7 — Backup e restauração

Tarefas:

- [ ] Criar comando `postgres_backup` ou adaptar `golive_backup`.
- [ ] Gerar `pg_dump --format=custom`.
- [ ] Gerar `tar.gz` do volume de mídia.
- [ ] Gerar manifesto JSON.
- [ ] Restaurar em banco limpo de teste.
- [ ] Rodar smoke tests após restore.

Critério de saída:

- Restauração comprovada em ambiente limpo.

### Fase 8 — Remoção de compatibilidade antiga

Tarefas:

- [ ] Remover fallback SQLite de produção.
- [ ] Remover campos BLOB antigos.
- [ ] Remover comandos/documentação SQLite de produção.
- [ ] Atualizar PRD principal e docs de arquitetura.

Critério de saída:

- Documentação e código apontam PostgreSQL como padrão de produção.

---

## 11. Plano de testes

### 11.1 Testes automatizados obrigatórios

Rodar:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Contra PostgreSQL:

```bash
docker compose run --rm app python manage.py check
docker compose run --rm app python manage.py makemigrations --check --dry-run
docker compose run --rm app python manage.py test
```

### 11.2 Testes funcionais manuais

| Fluxo | Passos | Resultado esperado |
---|---|---|
| Login | acessar `/login/`, autenticar | Dashboard abre |
| Cliente | criar cliente com CPF/RG/telefone | Cliente salvo e encontrado por busca normalizada |
| Produto | criar categoria/produto | Produto aparece por prefixo+código |
| Locação | criar locação com 2 itens | Total calculado, número gerado |
| Foto | anexar foto em item | Foto salva em `MEDIA_ROOT`, não no banco |
| Retirada | registrar retirada | Status `Retirado` |
| Devolução | registrar devolução | Status `Devolvido`, atraso calculado |
| Pagamento | pagar recebível | Payment + movement vinculados |
| Estorno | estornar pagamento | Reversal + movement vinculados |
| Relatório | abrir A Retirar/Retirados/Recebíveis | Resposta sem erro e sem lentidão visível |
| Backup | executar backup | Dump, mídia e manifesto criados |
| Restore | restaurar em banco limpo | Contagens batem e app inicia |

### 11.3 Testes de performance mínimos

Com base importada:

- Lista de clientes com busca por RG: até 1s.
- Autocomplete de cliente: até 500ms em rede local/VPS.
- Lista de produtos com prefixo+código: até 500ms.
- Relatório A Retirar com limite padrão: até 1s.
- Dashboard: até 1s.

### 11.4 Consultas de diagnóstico

Tamanho do banco:

```sql
SELECT pg_size_pretty(pg_database_size(current_database()));
```

Maiores tabelas:

```sql
SELECT
  relname AS table_name,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 20;
```

Índices não usados, depois de algum uso real:

```sql
SELECT
  schemaname,
  relname,
  indexrelname,
  idx_scan
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC
LIMIT 50;
```

Conexões:

```sql
SELECT state, count(*)
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state;
```

---

## 12. Plano de rollback

Como o sistema está em teste, rollback deve ser simples e explícito.

### 12.1 Antes do go-live

Rollback permitido:

- derrubar Postgres;
- voltar `.env` para SQLite local;
- restaurar `db.sqlite3` de backup;
- rodar app em modo desenvolvimento.

### 12.2 Depois do go-live

Rollback recomendado:

- não voltar para SQLite;
- restaurar último `pg_dump` validado;
- restaurar backup de mídia correspondente;
- reaplicar versão anterior do código;
- rodar smoke tests.

### 12.3 Critérios para abortar migração

Abortar se:

- migrations não aplicarem do zero;
- importação gerar divergência de contagem sem explicação;
- upload de fotos falhar;
- pagamento/estorno falhar em teste;
- restore não reproduzir contagens;
- performance básica piorar visivelmente nas listas críticas.

---

## 13. Operação na VPS Ubuntu Server

### 13.1 Comandos essenciais

Subir:

```bash
docker compose up -d --build
```

Ver saúde:

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f db
```

Aplicar migrations:

```bash
docker compose exec app python manage.py migrate
```

Criar usuário admin:

```bash
docker compose exec app python manage.py createsuperuser
```

Entrar no banco:

```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

Backup:

```bash
docker compose exec db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file=/tmp/noivas.dump
docker compose cp db:/tmp/noivas.dump ./backups/noivas.dump
```

Backup de mídia:

```bash
docker run --rm -v noivas-cia_media_data:/media -v "$PWD/backups:/backup" alpine \
  tar -czf /backup/media.tar.gz -C /media .
```

Restore:

```bash
docker compose exec db dropdb -U "$POSTGRES_USER" "$POSTGRES_DB"
docker compose exec db createdb -U "$POSTGRES_USER" "$POSTGRES_DB"
docker compose cp ./backups/noivas.dump db:/tmp/noivas.dump
docker compose exec db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/noivas.dump
```

### 13.2 Segurança operacional

Regras:

- Não publicar porta 5432 no host.
- Usar senha forte em `POSTGRES_PASSWORD`.
- Manter `.env` fora do Git.
- Backup deve sair da VPS periodicamente.
- Testar restore antes do go-live.
- Monitorar disco com `df -h`.
- Monitorar tamanho de volumes Docker.

---

## 14. Riscos e mitigação

| Risco | Impacto | Mitigação |
|---|---|---|
| Migration complexa com dados de teste inconsistentes | Médio | Reset limpo e reimportação |
| Duplicados em produto `(category, code)` | Médio | Relatório de duplicados antes de constraint |
| Erro ao mover fotos para FileField | Médio | Sistema em teste permite descartar fotos; comando opcional de exportação |
| Configuração errada de `DATABASE_URL` | Alto | Falhar rápido em produção; README com exemplo |
| Backup sem mídia | Alto | Manifesto deve listar banco e mídia |
| Exposição do Postgres na VPS | Alto | Não publicar porta 5432; rede interna Compose |
| Busca trigram indisponível | Baixo | Migration habilita `pg_trgm`; fallback por B-tree em documentos |
| Testes lentos em Postgres | Baixo | Usar banco de teste Docker local; manter fixtures pequenas |

---

## 15. Critérios de aceite final

A migração será considerada concluída quando:

- [ ] App sobe com PostgreSQL via Docker Compose.
- [ ] `python manage.py migrate` roda em banco PostgreSQL limpo.
- [ ] `python manage.py makemigrations --check --dry-run` não detecta mudanças.
- [ ] `python manage.py test` passa em PostgreSQL.
- [ ] Login, cliente, produto, locação, retirada, devolução, pagamento, estorno e relatórios funcionam.
- [ ] Busca por RG/CPF/telefone usa campos normalizados persistidos.
- [ ] Fotos de comprovação são arquivos em `MEDIA_ROOT`, não BLOB no banco.
- [ ] Backup de banco e mídia é gerado.
- [ ] Restore foi testado em ambiente limpo.
- [ ] README documenta execução, backup, restore e operação básica.
- [ ] `.env.example` possui variáveis de Postgres e mídia.
- [ ] O banco PostgreSQL não fica exposto publicamente na VPS.

---

## 16. Sequência recomendada de commits

1. `feat(db): add postgres configuration`
   - dependências;
   - settings;
   - `.env.example`;
   - README.

2. `feat(db): add postgres compose service`
   - `docker-compose.yml`;
   - volumes;
   - healthchecks.

3. `feat(search): persist normalized lookup fields`
   - campos normalizados;
   - data migration;
   - testes de busca.

4. `feat(media): move proof photos to file storage`
   - `FileField`;
   - form upload;
   - view protegida;
   - volume `media_data`;
   - testes.

5. `perf(db): add postgres indexes`
   - B-tree;
   - partial indexes;
   - trigram indexes.

6. `feat(ops): add postgres backup and restore docs`
   - comando de backup;
   - manifesto;
   - restore drill.

7. `test(db): run suite against postgres`
   - ajustes de testes;
   - documentação do comando.

---

## 17. Checklist de execução rápida

```bash
# 1. Preparar env
cp .env.example .env
# editar POSTGRES_PASSWORD, DJANGO_SECRET_KEY, hosts e origens

# 2. Subir banco
docker compose up -d db

# 3. Migrar
docker compose run --rm app python manage.py migrate

# 4. Criar admin
docker compose run --rm app python manage.py createsuperuser

# 5. Testar
docker compose run --rm app python manage.py test

# 6. Subir app
docker compose up -d --build

# 7. Validar healthcheck
curl -fsS http://127.0.0.1:8000/healthz/

# 8. Gerar backup de validação
docker compose exec db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file=/tmp/noivas.dump
```

---

## 18. Decisão final

Como o sistema ainda está em teste, a recomendação é **não investir em migração incremental perfeita do SQLite atual**. O caminho mais seguro e limpo é:

1. implementar PostgreSQL e mídia em arquivos;
2. recriar banco do zero;
3. reimportar legado ou fixtures validadas;
4. testar restore;
5. seguir para homologação real já com a arquitetura final.

Isso reduz complexidade, evita carregar decisões temporárias para produção e deixa a VPS preparada para operação com múltiplos usuários.

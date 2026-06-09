# Noivas & Cia

Sistema de gerenciamento comercial e locacao para aluguel de roupas e acessorios para eventos. Django full-stack com TailwindCSS e SQLite.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12+ / Django 5.x |
| Frontend | Django Template Language + TailwindCSS 3.x |
| Banco | SQLite |
| Container | Docker (opcional) |

## Execucao local

### Requisitos

- Python 3.12+
- Node.js 20+ (para TailwindCSS)

### Instalacao

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
npm install
npm run build:css
```

### Rodando

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Acesse http://127.0.0.1:8000

### Comandos uteis

```bash
npm run watch:css          # Rebuild CSS ao salvar
python manage.py test      # Rodar testes
python manage.py check     # Verificacao do Django
python manage.py makemigrations <app>  # Criar migracoes
```

## Execucao com Docker

### Requisitos

- Docker
- Docker Compose

### Configuracao

```bash
cp .env.example .env
```

Edite o `.env` com uma `DJANGO_SECRET_KEY` segura para producao.

### Build e execucao

```bash
docker compose up --build
```

Acesse http://localhost:8000

### Comandos no container

```bash
docker compose exec app python manage.py migrate
docker compose exec app python manage.py createsuperuser
docker compose exec app python manage.py test
docker compose exec app python manage.py collectstatic --noinput
```

### Variaveis de ambiente

| Variavel | Padrao | Descricao |
|---|---|---|
| `DJANGO_SECRET_KEY` | chave insegura de dev | Chave secreta do Django |
| `DJANGO_DEBUG` | `True` | Modo debug |
| `DJANGO_ALLOWED_HOSTS` | (vazio) | Hosts permitidos, separados por virgula |
| `DATABASE_NAME` | `db.sqlite3` | Caminho do banco SQLite |
| `STATIC_ROOT` | `staticfiles/` | Destino da coleta de estaticos |

## Estrutura do projeto

```
noivas-cia/
  accounts/        # Usuario customizado e permissoes
  billing/         # Recebimentos e juros
  catalog/         # Categorias, produtos e disponibilidade
  company/         # Configuracao singleton da empresa
  core/            # Base abstrata, dashboard e mixins
  customers/       # Cadastro de clientes
  maintenance/     # Rotinas administrativas
  movements/       # Retiradas e devolucoes
  noivas_cia/      # Settings e URLs do projeto
  rentals/         # Locacoes e itens
  reports/         # Relatorios de acompanhamento
  static/          # CSS, assets e source Tailwind
  templates/       # Templates DTL (base, includes e por app)
  website/         # Site publico institucional
```

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

Edite o `.env` antes de subir:

- troque `DJANGO_SECRET_KEY` por uma chave forte;
- preencha `DJANGO_ALLOWED_HOSTS` com o dominio publico e mantenha `localhost,127.0.0.1` se usar o healthcheck do Compose;
- preencha `CSRF_TRUSTED_ORIGINS` com origens HTTPS publicas, por exemplo `https://app.example.com`;
- se for acessar o container diretamente por HTTP local, sem proxy TLS, defina `DJANGO_SECURE_SSL_REDIRECT=False`.

### Build e execucao

```bash
docker compose up --build
```

Acesse http://localhost:8000

Por padrao o Compose publica a porta apenas em `127.0.0.1`. Coloque um proxy reverso local
na frente do container para expor o sistema publicamente.

### Comandos no container

```bash
docker compose exec app python manage.py migrate
docker compose exec app python manage.py createsuperuser
docker compose exec app python manage.py test
docker compose exec app python manage.py collectstatic --noinput
docker compose exec app python manage.py golive_backup
```

O comando `golive_backup` usa a API de backup do SQLite e grava em `BACKUP_ROOT`
(`/app/data/backups` no Docker), junto do manifesto com hash SHA-256 e contagens.

### Variaveis de ambiente

| Variavel | Padrao | Descricao |
|---|---|---|
| `DJANGO_SECRET_KEY` | obrigatoria em producao | Chave secreta do Django |
| `DJANGO_DEBUG` | `False` | Modo debug |
| `DJANGO_ALLOWED_HOSTS` | obrigatoria em producao | Hosts permitidos, separados por virgula |
| `CSRF_TRUSTED_ORIGINS` | (vazio) | Origens HTTPS confiaveis para CSRF, separadas por virgula |
| `DJANGO_SECURE_SSL_REDIRECT` | `True` em producao | Redireciona HTTP para HTTPS |
| `DJANGO_TRUST_X_FORWARDED_PROTO` | `True` em producao | Confia no header `X-Forwarded-Proto` do proxy |
| `SESSION_COOKIE_SECURE` | `True` em producao | Envia cookie de sessao apenas via HTTPS |
| `CSRF_COOKIE_SECURE` | `True` em producao | Envia cookie CSRF apenas via HTTPS |
| `SECURE_HSTS_SECONDS` | `31536000` em producao | HSTS em segundos; use `0` para desativar |
| `DATABASE_NAME` | `db.sqlite3` | Caminho do banco SQLite |
| `STATIC_ROOT` | `staticfiles/` | Destino da coleta de estaticos |
| `BACKUP_ROOT` | `var/backups` | Diretorio de backups SQLite; no Docker fica em `/app/data/backups` |
| `USER_CREATOR_EMAILS` | (vazio) | E-mails com permissao para criar e gerenciar usuarios (separados por virgula) |

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

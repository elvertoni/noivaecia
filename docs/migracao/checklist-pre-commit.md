# Checklist Pre-Commit - Migracao BRcom

Use este checklist antes de qualquer commit relacionado a migracao.

## Checklist

- [ ] `brcom/` nao aparece no Git: snapshot legado, `.mdb`, `.rpt`, `.exe`, `.dll`, `.ocx`, `.ldb` ficam fora do commit.
- [ ] `db.sqlite3` nao esta staged nem tracked.
- [ ] `var/` nao esta staged: inclui `var/legacy_export`, JSONL, manifests, relatorios, dumps e diagnosticos com dados reais.
- [ ] `staticfiles/` e `static/css/output.css` nao estao staged.
- [ ] `.env` nao esta staged.
- [ ] `.env.example` contem apenas placeholders, sem senha real, chave real, SMTP real ou host de producao sensivel.
- [ ] Dumps/backups nao estao staged: `*.sql`, `*.sqlite`, `*.sqlite3`, `*.bak`, `*.zip`, `*.7z`, `*.csv`, `*.jsonl`, `manifest.json` de carga real.
- [ ] Prints/screenshots nao expoem cliente, CPF, RG, telefone, endereco, valores, titulos, locacoes ou dados do Access.
- [ ] Logs nao estao staged: `*.log`, traceback com env, SQL dump, paths pessoais ou dados de clientes.
- [ ] Nenhum dado sensivel aparece no diff: clientes, financeiro, caminhos pessoais, e-mails/senhas de producao.
- [ ] Artefatos permitidos no commit: codigo, docs/checklists sem dados reais e `.env.example` sanitizado.

## Comandos de verificacao

```powershell
git status --short
git diff --cached --name-only
git check-ignore -v brcom brcom/brcom.mdb db.sqlite3 var staticfiles .env static/css/output.css
git ls-files brcom db.sqlite3 var staticfiles .env
git diff --cached -- . ':!*.lock' | rg -i "cpf|rg|telefone|celular|senha|password|secret|token|DJANGO_SECRET_KEY|EMAIL_HOST_PASSWORD|brcom\.mdb|\.mdb|\.rpt|db\.sqlite3|legacy_export|staticfiles|var/"
git diff --cached --name-only | rg -i "brcom|\.mdb|\.rpt|\.exe|\.dll|\.ocx|\.ldb|db\.sqlite3|^var/|^staticfiles/|\.env$|\.log$|\.sql$|\.bak$|\.zip$|\.7z$|\.csv$|\.jsonl$|print|screenshot|dump"
```

## Regra de decisao

Se qualquer comando apontar artefato legado, banco local, dump, dado real ou segredo, remova do stage antes de continuar.

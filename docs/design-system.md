# Design system

Identidade visual unica, clara e operacional. A interface usa base neutra, alta legibilidade e acento de marca controlado para acoes principais, foco e links. O objetivo visual e apoiar atendimento rapido, leitura de tabelas, preenchimento de formularios e conferencia de valores, sem decoracao que dispute atencao com os dados.

## Paleta de cores

| Token        | Uso                            | Hex       | Tailwind      |
|-------------|--------------------------------|-----------|---------------|
| Background  | Fundo da aplicacao             | `#F6F7F9` | `app-bg`      |
| Surface     | Paineis, tabelas, formularios  | `#FFFFFF` | `app-surface` |
| Panel       | Areas sutis e tiles internos   | `#F8FAFC` | `app-panel`   |
| Ink         | Texto principal                | `#111827` | `app-ink`     |
| Muted       | Texto secundario               | `#475569` | `app-muted`   |
| Border      | Bordas funcionais              | `#CBD5E1` | `app-border`  |
| Brand       | Acoes primarias e links        | `#9F1239` | `brand-700`   |
| Brand hover | Hover/active primario          | `#881337` | `brand-800`   |
| Focus       | Anel de foco                   | `#FDA4B5` | `brand-300`   |
| Success     | Confirmacoes                   | emerald-800 sobre emerald-50  | `badge-success`, `alert-success` |
| Danger      | Erros e exclusoes              | red-700/800 sobre red-50       | `badge-danger`, `alert-error`    |
| Warning     | Atencao                        | amber-800 sobre amber-50       | `badge-warning`, `alert-warning` |
| Info        | Informacao neutra              | sky-800 sobre sky-50           | `badge-info`, `alert-info`       |

Gradientes nao sao padrao do produto. O acento de marca aparece apenas em acoes principais, links, foco e pequenos estados de selecao.

## Tipografia

- Fonte: **Inter** (fallback `system-ui, sans-serif`).
- Titulos de pagina: `page-title` (`text-2xl`, peso 600, `text-app-ink`).
- Subtitulos e descricoes: `page-description` (`text-sm`, altura de linha 1.5, `text-app-muted`).
- Corpo e dados: `text-sm text-slate-700`; valores importantes usam `font-medium` ou `font-semibold`.
- Rotulos de formulario: `field-label`, sem caixa alta e com contraste suficiente.
- Indicadores: `stat-label` + `stat-value`, com peso forte apenas no valor.
- Cabecalhos de tabela: `text-xs font-semibold text-slate-600`, sem caixa alta obrigatoria.

## Componentes

### Botoes

```html
<!-- Primario -->
<button class="btn btn-primary">Salvar</button>

<!-- Secundario -->
<button class="btn btn-secondary">Cancelar</button>

<!-- Perigo -->
<button class="btn btn-danger">Excluir</button>
```

### Inputs e forms

```html
<div class="field-group">
  <label class="field-label">Nome do cliente</label>
  <input type="text" class="field-input" placeholder="Digite o nome">
  <p class="field-help">Texto auxiliar opcional.</p>
  <p class="field-error">Mensagem de erro.</p>
</div>

<form class="grid grid-cols-1 gap-4 md:grid-cols-2">
  <!-- campos -->
</form>
```

### Cards e grids

```html
<div class="panel-spacious">
  <h2 class="text-lg font-semibold text-app-ink">Titulo do painel</h2>
  <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
    <!-- itens -->
  </div>
</div>
```

Use `panel` para paineis compactos, `panel-spacious` para formularios e blocos principais, `surface-section` para secoes leves e `module-tile` para atalhos internos. Evite borda e sombra no mesmo componente.

### Menu / navegacao

```html
<aside class="flex h-screen w-64 flex-col border-r border-slate-800 bg-slate-950 text-slate-100">
  <div class="px-4 py-4"><img src="..." alt="Noivas & Cia" class="h-12 w-auto"></div>
  <nav class="flex-1 space-y-1 px-3">
    <p class="nav-section">Cadastros</p>
    <a href="#" class="nav-link">Clientes</a>
    <a href="#" class="nav-link">Produtos</a>
  </nav>
</aside>
```

### Tabelas (listagens)

```html
<div class="table-shell">
  <table class="data-table">
    <thead>
      <tr><th>Prefixo</th><th>Categoria</th></tr>
    </thead>
    <tbody>
      <tr><td>VN</td><td>Vestidos de Noivas</td></tr>
      <tr><td colspan="2" class="table-empty">Nenhum resultado.</td></tr>
    </tbody>
  </table>
</div>
```

Todos os componentes acima vivem em `static/src/input.css` dentro de `@layer components`. Campos gerados por forms Django devem usar `core.ui.INPUT_CLASS`, que aponta para `field-input`.

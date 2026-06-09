# Design system

Identidade visual única, moderna, de fundo claro, com gradientes e paleta adequada ao nicho de noivas e eventos. Todas as telas reutilizam os mesmos componentes via parciais (`templates/includes/`) e classes utilitárias Tailwind, incluídos com `{% include %}`.

## Paleta de cores

| Token | Uso | Hex | Tailwind |
|---|---|---|---|
| Primary | Ações principais, marca | `#F43F5E` → `#EC4899` | `rose-500` → `pink-500` |
| Secondary | Acentos sóbrios, links | `#7C3AED` | `violet-600` |
| Accent | Destaques pontuais | `#FBBF24` | `amber-400` |
| Success | Confirmações | `#10B981` | `emerald-500` |
| Danger | Exclusões, erros | `#EF4444` | `red-500` |
| Background | Fundo da aplicação | gradiente claro | `from-rose-50 via-white to-pink-50` |
| Surface | Cards e painéis | `#FFFFFF` | `white` |
| Text | Texto principal | `#1E293B` | `slate-800` |
| Muted | Texto secundário | `#64748B` | `slate-500` |
| Border | Bordas | `#E2E8F0` | `slate-200` |

- Gradiente de marca padrão: `bg-gradient-to-r from-rose-500 to-pink-500`.
- Fundo de página padrão: `bg-gradient-to-br from-rose-50 via-white to-pink-50`.

## Tipografia

- Fonte: **Inter** (fallback `system-ui, sans-serif`).
- Títulos: `text-2xl font-bold text-slate-800` (h1), `text-xl font-semibold` (h2).
- Corpo: `text-sm text-slate-700`.
- Rótulos: `text-xs font-medium uppercase tracking-wide text-slate-500`.

## Componentes

### Botões

```html
<!-- Primário -->
<button class="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-rose-500 to-pink-500 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:from-rose-600 hover:to-pink-600 focus:outline-none focus:ring-2 focus:ring-rose-300">
  Salvar
</button>

<!-- Secundário -->
<button class="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50">
  Cancelar
</button>

<!-- Perigo -->
<button class="rounded-lg bg-red-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-600">
  Excluir
</button>
```

### Inputs e forms

```html
<div class="space-y-1">
  <label class="text-xs font-medium uppercase tracking-wide text-slate-500">Nome do cliente</label>
  <input type="text"
         class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm focus:border-rose-400 focus:ring-2 focus:ring-rose-200 focus:outline-none"
         placeholder="Digite o nome">
</div>

<!-- Form em grid responsivo -->
<form class="grid grid-cols-1 gap-4 md:grid-cols-2">
  <!-- campos -->
</form>
```

### Cards e grids

```html
<div class="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
  <h2 class="text-xl font-semibold text-slate-800">Título do painel</h2>
  <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
    <!-- itens -->
  </div>
</div>
```

### Menu / navegação

```html
<aside class="flex h-screen w-64 flex-col bg-gradient-to-b from-slate-900 to-slate-800 text-slate-100">
  <div class="px-6 py-5 text-lg font-bold">Noivas &amp; Cia</div>
  <nav class="flex-1 space-y-1 px-3">
    <a href="#" class="block rounded-lg px-3 py-2 text-sm hover:bg-white/10">Dashboard</a>
    <a href="#" class="block rounded-lg px-3 py-2 text-sm hover:bg-white/10">Cadastros</a>
    <a href="#" class="block rounded-lg px-3 py-2 text-sm hover:bg-white/10">Movimentação</a>
    <a href="#" class="block rounded-lg px-3 py-2 text-sm hover:bg-white/10">Financeiro</a>
    <a href="#" class="block rounded-lg px-3 py-2 text-sm hover:bg-white/10">Relatórios</a>
  </nav>
</aside>
```

### Tabelas (listagens)

```html
<table class="min-w-full divide-y divide-slate-200 text-sm">
  <thead class="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
    <tr><th class="px-4 py-3 text-left">Prefixo</th><th class="px-4 py-3 text-left">Categoria</th></tr>
  </thead>
  <tbody class="divide-y divide-slate-100">
    <tr class="hover:bg-rose-50/50"><td class="px-4 py-3">VN</td><td class="px-4 py-3">Vestidos de Noivas</td></tr>
  </tbody>
</table>
```

Todos os componentes acima são extraídos para parciais reutilizáveis em `templates/includes/` e incluídos via `{% include %}`, garantindo identidade visual única.

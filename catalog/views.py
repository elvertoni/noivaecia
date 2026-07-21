import re
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from core.mixins import ModuleAccessMixin, ActionRequiredMixin
from core.models import AuditLog
from core.ui import parse_br_date

from rentals.models import RentalItem
from customers.models import _normalize_name

from .availability import INACTIVE_RENTAL_STATUSES, find_overlapping_rental, find_rental_for
from .forms import CategoryForm, CategoryMergeForm, ProductForm
from .models import Category, Product


class CatalogAccessMixin(ModuleAccessMixin):
    module_key = 'catalog'


# ── Categories ────────────────────────────────────────────────────────────────

class CategoryListView(CatalogAccessMixin, ListView):
    model = Category
    template_name = 'catalog/category_list.html'
    context_object_name = 'categories'
    paginate_by = 30

    def get_queryset(self):
        qs = super().get_queryset().annotate(product_count=Count('products'))
        q = self.request.GET.get('q', '').strip()
        only_placeholders = self.request.GET.get('placeholder', '')
        if q:
            qs = qs.filter(Q(prefix__icontains=q) | Q(name__icontains=q))
        if only_placeholders:
            qs = qs.filter(is_placeholder=True)
        # Explicit order so pagination is deterministic (annotate can drop it).
        return qs.order_by('prefix')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        ctx['only_placeholders'] = self.request.GET.get('placeholder', '')
        ctx['placeholder_count'] = Category.objects.filter(is_placeholder=True).count()
        return ctx


class CategoryCreateView(CatalogAccessMixin, SuccessMessageMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = 'catalog/category_form.html'
    success_url = reverse_lazy('catalog:category_list')
    success_message = 'Categoria cadastrada com sucesso.'


class CategoryUpdateView(CatalogAccessMixin, SuccessMessageMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = 'catalog/category_form.html'
    success_url = reverse_lazy('catalog:category_list')
    success_message = 'Categoria atualizada com sucesso.'


class CategoryDeleteView(CatalogAccessMixin, ActionRequiredMixin, DeleteView):
    action_key = 'catalog.delete'
    model = Category
    template_name = 'catalog/category_confirm_delete.html'
    success_url = reverse_lazy('catalog:category_list')

    def form_valid(self, form):
        category = self.get_object()
        AuditLog.objects.create(
            user=self.request.user,
            action='category_delete',
            model_name='Category',
            object_id=str(category.pk),
            object_repr=str(category),
            reason='Exclusão de categoria.',
        )
        messages.success(self.request, 'Categoria excluída com sucesso.')
        return super().form_valid(form)


# ── Products ──────────────────────────────────────────────────────────────────

class ProductListView(CatalogAccessMixin, ListView):
    """Product listing with extended filters (R8.01/R8.02)."""

    model = Product
    template_name = 'catalog/product_list.html'
    context_object_name = 'products'
    paginate_by = 30

    def get_queryset(self):
        duplicate = Product.objects.filter(
            category_id=OuterRef('category_id'),
            code=OuterRef('code'),
        ).exclude(pk=OuterRef('pk'))
        qs = (
            super().get_queryset()
            .select_related('category')
            .annotate(is_duplicate=Exists(duplicate))
        )

        prefix = self.request.GET.get('prefix', '').strip()
        code = self.request.GET.get('code', '').strip()
        description = self.request.GET.get('description', '').strip()
        color = self.request.GET.get('color', '').strip()
        size = self.request.GET.get('size', '').strip()
        only_placeholder = self.request.GET.get('placeholder', '')
        only_duplicate = self.request.GET.get('duplicate', '')

        if prefix:
            qs = qs.filter(category__prefix__icontains=prefix)
        if code:
            try:
                val = int(code)
                if val > 2147483647:
                    qs = qs.none()
                else:
                    qs = qs.filter(code=val)
            except ValueError:
                qs = qs.none()
        if description:
            qs = qs.filter(description_search__icontains=_normalize_name(description))
        if color:
            qs = qs.filter(color__icontains=color)
        if size:
            qs = qs.filter(size__icontains=size)
        if only_placeholder:
            qs = qs.filter(is_placeholder=True)
        if only_duplicate:
            qs = qs.filter(is_duplicate=True)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        visible_products = ctx['object_list']
        ctx.update({
            'prefix': self.request.GET.get('prefix', ''),
            'code': self.request.GET.get('code', ''),
            'description': self.request.GET.get('description', ''),
            'color': self.request.GET.get('color', ''),
            'size': self.request.GET.get('size', ''),
            'only_placeholder': self.request.GET.get('placeholder', ''),
            'only_duplicate': self.request.GET.get('duplicate', ''),
            'categories': Category.objects.all(),
            'placeholder_count': Product.objects.filter(is_placeholder=True).count(),
            'duplicate_ids': {
                product.pk for product in visible_products if product.is_duplicate
            },
        })
        return ctx


class ProductCreateView(CatalogAccessMixin, SuccessMessageMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = 'catalog/product_form.html'
    success_url = reverse_lazy('catalog:product_list')
    success_message = 'Produto cadastrado com sucesso.'


class ProductUpdateView(CatalogAccessMixin, SuccessMessageMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = 'catalog/product_form.html'
    success_url = reverse_lazy('catalog:product_list')
    success_message = 'Produto atualizado com sucesso.'


class ProductDeleteView(CatalogAccessMixin, ActionRequiredMixin, DeleteView):
    action_key = 'catalog.delete'
    model = Product
    template_name = 'catalog/product_confirm_delete.html'
    success_url = reverse_lazy('catalog:product_list')

    def form_valid(self, form):
        product = self.get_object()
        AuditLog.objects.create(
            user=self.request.user,
            action='product_delete',
            model_name='Product',
            object_id=str(product.pk),
            object_repr=str(product),
            reason='Exclusão de produto.',
        )
        messages.success(self.request, 'Produto excluído com sucesso.')
        return super().form_valid(form)


class ProductHistoryView(CatalogAccessMixin, DetailView):
    """Recent rentals for a product (R8.04)."""

    model = Product
    template_name = 'catalog/product_history.html'
    context_object_name = 'product'

    def get_queryset(self):
        return super().get_queryset().select_related('category')

    def get_context_data(self, **kwargs):
        from rentals.models import RentalItem
        ctx = super().get_context_data(**kwargs)
        # Detect siblings (same prefix+code) for duplicate warning
        siblings = Product.objects.filter(
            category=self.object.category,
            code=self.object.code,
        ).exclude(pk=self.object.pk)
        ctx['siblings'] = list(siblings)
        # Recent rental items — latest 50
        rental_items = (
            RentalItem.objects.filter(product=self.object)
            .select_related('rental', 'rental__customer')
            .defer('proof_photo')
            .order_by('-rental__pickup_date', '-rental__number')[:50]
        )
        ctx['rental_items'] = rental_items
        return ctx


# ── Availability ──────────────────────────────────────────────────────────────

class AvailabilityView(CatalogAccessMixin, TemplateView):
    """Check availability — handles duplicate products with disambiguation (R8.03)."""

    template_name = 'catalog/availability.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prefix = self.request.GET.get('prefix', '').strip()
        code = self.request.GET.get('code', '').strip()
        date_str = self.request.GET.get('date', '').strip()
        product_id = self.request.GET.get('product_id', '').strip()

        context['prefix'] = prefix
        context['code'] = code
        context['date'] = date_str

        if not (prefix and code and date_str):
            return context

        on_date = parse_br_date(date_str)
        if on_date is None:
            context['error'] = 'Data inválida.'
            return context

        # ``code`` is received as text and can otherwise make the integer
        # model lookup raise a ValueError instead of showing a form error.
        numeric_code = code.lstrip('0') or '0'
        if not code.isdigit() or len(numeric_code) > 10 or int(numeric_code) > 2147483647:
            context['error'] = 'Informe um código de produto válido.'
            return context

        products = list(
            Product.objects.filter(category__prefix__iexact=prefix, code=int(numeric_code))
            .select_related('category')
        )
        if not products:
            context['error'] = 'Produto não encontrado.'
            return context

        # R8.03 — disambiguation when duplicates exist
        if len(products) > 1 and not product_id:
            context['needs_disambiguation'] = True
            context['candidates'] = products
            return context

        # Resolve which product to check
        if product_id and len(products) > 1:
            product = next((p for p in products if str(p.pk) == product_id), products[0])
        else:
            product = products[0]

        context['product'] = product
        context['checked'] = True
        context['rental'] = find_rental_for(product, on_date)
        return context


# ── Placeholder review (R8.05) ────────────────────────────────────────────────

class PlaceholderReviewView(CatalogAccessMixin, TemplateView):
    """List placeholder categories and products for admin review (R8.05)."""

    template_name = 'catalog/placeholder_review.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['placeholder_categories'] = (
            Category.objects.filter(is_placeholder=True)
            .annotate(product_count=Count('products'))
            .order_by('prefix')
        )
        ctx['placeholder_products'] = (
            Product.objects.filter(is_placeholder=True)
            .select_related('category')
            .order_by('category__prefix', 'code')
        )
        return ctx


# ── Category merge (R8.06) ────────────────────────────────────────────────────

class CategoryMergeView(CatalogAccessMixin, View):
    """Merge source category into target, updating all products/items (R8.06).

    GET/POST preview=1: show impact preview.
    POST confirmed=1: execute in atomic transaction.
    """

    template_name = 'catalog/category_merge.html'

    def get(self, request, *args, **kwargs):
        source_id = request.GET.get('source', '')
        initial = {'source': source_id} if source_id.isdigit() else None
        form = CategoryMergeForm(initial=initial)
        return self._render(request, form)

    def post(self, request, *args, **kwargs):
        form = CategoryMergeForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)

        source = form.cleaned_data['source']
        target = form.cleaned_data['target']
        confirmed = request.POST.get('confirmed') == '1'

        product_count = Product.objects.filter(category=source).count()

        # Count rental items affected via products in source category
        from rentals.models import RentalItem
        item_count = RentalItem.objects.filter(product__category=source).count()

        if not confirmed:
            # Show preview
            return self._render(request, form, preview={
                'source': source,
                'target': target,
                'product_count': product_count,
                'item_count': item_count,
            })

        # Execute merge
        with transaction.atomic():
            Product.objects.filter(category=source).update(category=target)
            AuditLog.objects.create(
                user=request.user,
                action='category_merge',
                model_name='Category',
                object_id=str(source.pk),
                object_repr=f'{source.prefix} → {target.prefix}',
                reason=f'Mesclagem de categoria: {product_count} produtos, {item_count} itens atualizados.',
            )
            # Delete source if now empty
            if not Product.objects.filter(category=source).exists():
                source_prefix = source.prefix
                source.delete()
                messages.success(
                    request,
                    f'Categoria {source_prefix} mesclada em {target.prefix}. '
                    f'{product_count} produto(s) e {item_count} item(ns) atualizados. '
                    'Categoria de origem excluída.',
                )
            else:
                messages.warning(
                    request,
                    f'Mesclagem concluída mas categoria de origem ainda tem produtos. Verifique.',
                )

        return redirect('catalog:category_list')

    def _render(self, request, form, preview=None):
        from django.template.response import TemplateResponse
        return TemplateResponse(request, self.template_name, {
            'form': form,
            'preview': preview,
        })


# ── JSON search / availability ─────────────────────────────────────────────────

def picker_access(user):
    """Authenticated staff who can reach the rental or catalog modules.

    The product picker endpoints serve the rental form, so a rentals-only user
    must be allowed in — requiring the ``catalog`` module would break the rental
    flow — but an authenticated user with no relevant module cannot enumerate
    the catalog.
    """
    return user.is_authenticated and (
        user.has_module('rentals') or user.has_module('catalog')
    )


def product_text_filter(q):
    """Build the shared free-text/code Q filter for product lookups."""
    # description_search is accent-normalized and trigram-indexed
    # (product_desc_trgm_idx); query it with the same normalization so the
    # term actually matches the stored column and engages the GIN index.
    q_norm = _normalize_name(q)
    q_filter = (
        Q(description_search__icontains=q_norm)
        | Q(color__icontains=q)
        | Q(size__icontains=q)
        | Q(category__prefix__icontains=q)
    )
    code_match = re.match(r'^([A-Za-z]+)?\s*0*(\d+)$', q)
    if code_match:
        prefix, code = code_match.groups()
        numeric_code = code.lstrip('0') or '0'
        if len(numeric_code) <= 10:
            code_value = int(numeric_code)
            if code_value <= 2147483647:
                code_filter = Q(code=code_value)
                if prefix:
                    code_filter &= Q(category__prefix__iexact=prefix)
                q_filter |= code_filter
    return q_filter


class ProductSearchView(View):
    """JSON quick-search for product picker in rental item form (R7.03)."""

    def get(self, request, *args, **kwargs):
        if not picker_access(request.user):
            return JsonResponse({'results': []}, status=403)
        q = request.GET.get('q', '').strip()
        code_match = re.match(r'^([A-Za-z]+)?\s*0*(\d+)$', q)
        if len(q) < 2 and not code_match:
            return JsonResponse({'results': []})
        qs = (
            Product.objects.select_related('category')
            .filter(product_text_filter(q))
            .order_by('category__prefix', 'code')[:20]
        )
        results = [
            {
                'id': p.pk,
                'code': f'{p.category.prefix}{p.code}',
                'text': p.description,
                'sub': f'{p.color or "—"} · {p.size or "—"}',
                'value': str(p.value),
            }
            for p in qs
        ]
        return JsonResponse({'results': results})


class ProductBrowseView(View):
    """JSON faceted browse for the rental item picker modal.

    Cascading facets: ``q`` narrows categories; the chosen category narrows the
    size/color facets; size/color narrow the result grid. Availability for the
    given ``date`` is computed inline (single query) so 500+ items can be
    triaged at a glance instead of checked one by one.
    """

    PAGE_SIZE = 24
    COLOR_FACET_LIMIT = 40
    # Active rentals exclude both returned AND cancelled holds; shared with
    # find_rental_for so the picker and the availability screen never diverge.
    INACTIVE_STATUSES = INACTIVE_RENTAL_STATUSES

    def get(self, request, *args, **kwargs):
        if not picker_access(request.user):
            return JsonResponse({'results': []}, status=403)

        prefix = request.GET.get('prefix', '').strip()
        size = request.GET.get('size', '').strip()
        color = request.GET.get('color', '').strip()
        q = request.GET.get('q', '').strip()
        date_str = request.GET.get('date', '').strip()
        pickup_date_str = request.GET.get('pickup_date', '').strip() or date_str
        return_date_str = request.GET.get('return_date', '').strip() or pickup_date_str
        include_empty = request.GET.get('empty') == '1'
        try:
            page = max(1, int(request.GET.get('page', '1')))
        except (TypeError, ValueError):
            page = 1

        pickup_date = None
        return_date = None
        if pickup_date_str and return_date_str:
            pickup_date = parse_br_date(pickup_date_str)
            return_date = parse_br_date(return_date_str)

        # ``scoped``: q + visibility only (drives the category facet).
        scoped = Product.objects.select_related('category')
        if not include_empty:
            scoped = scoped.exclude(description='')
        if q:
            scoped = scoped.filter(product_text_filter(q))

        categories = list(
            scoped.values('category__prefix', 'category__name')
            .annotate(n=Count('id'))
            .order_by('-n', 'category__prefix')
        )

        # ``base``: category applied (drives the size/color facets).
        base = scoped.filter(category__prefix__iexact=prefix) if prefix else scoped
        sizes = list(
            base.exclude(size='').values('size')
            .annotate(n=Count('id')).order_by('size')
        )
        colors = list(
            base.exclude(color='').values('color')
            .annotate(n=Count('id')).order_by('-n')[:self.COLOR_FACET_LIMIT]
        )

        # ``results``: size + color narrowing.
        results_qs = base
        if size:
            results_qs = results_qs.filter(size__iexact=size)
        if color:
            results_qs = results_qs.filter(color__icontains=color)
        results_qs = results_qs.order_by('category__prefix', 'code')

        if pickup_date and return_date:
            active_item = (
                RentalItem.objects.filter(
                    product=OuterRef('pk'),
                    rental__pickup_date__lte=return_date,
                    rental__return_date__gte=pickup_date,
                )
                .exclude(rental__status__in=self.INACTIVE_STATUSES)
            )
            results_qs = results_qs.annotate(in_use=Exists(active_item))

        total = results_qs.count()
        num_pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = min(page, num_pages)
        start = (page - 1) * self.PAGE_SIZE
        page_items = list(results_qs[start:start + self.PAGE_SIZE])

        rental_map = (
            self._rentals_for_page(page_items, pickup_date, return_date)
            if pickup_date and return_date else {}
        )

        results = []
        for product in page_items:
            entry = {
                'id': product.pk,
                'code': f'{product.category.prefix}{product.code}',
                'text': product.description or '—',
                'color': product.color,
                'size': product.size,
                'value': str(product.value),
            }
            if pickup_date and return_date:
                in_use = getattr(product, 'in_use', False)
                entry['available'] = not in_use
                rental = rental_map.get(product.pk)
                if in_use and rental:
                    entry['rental'] = {
                        'number': rental.number,
                        'customer': rental.customer.name,
                        'pickup_date': rental.pickup_date.isoformat(),
                        'return_date': rental.return_date.isoformat(),
                    }
            results.append(entry)

        return JsonResponse({
            'results': results,
            'page': page,
            'num_pages': num_pages,
            'total': total,
            'categories': categories,
            'facets': {'sizes': sizes, 'colors': colors},
        })

    def _rentals_for_page(self, page_items, pickup_date, return_date):
        in_use_ids = [p.pk for p in page_items if getattr(p, 'in_use', False)]
        if not in_use_ids:
            return {}
        items = (
            RentalItem.objects.filter(
                product_id__in=in_use_ids,
                rental__pickup_date__lte=return_date,
                rental__return_date__gte=pickup_date,
            )
            .exclude(rental__status__in=self.INACTIVE_STATUSES)
            .select_related('rental', 'rental__customer')
            # Deterministic: match the overlap validator when a piece has holds.
            .order_by('rental__pickup_date', 'rental__number')
        )
        rental_map = {}
        for item in items:
            rental_map.setdefault(item.product_id, item.rental)
        return rental_map


class ProductAvailabilityJsonView(View):
    """JSON availability check for a product over the rental window (R7.04)."""

    def get(self, request, *args, **kwargs):
        if not picker_access(request.user):
            return JsonResponse({'available': False, 'error': 'auth'}, status=403)
        product_id = request.GET.get('product_id', '').strip()
        date_str = request.GET.get('date', '').strip()
        pickup_date_str = request.GET.get('pickup_date', '').strip() or date_str
        return_date_str = request.GET.get('return_date', '').strip() or pickup_date_str
        if not product_id or not pickup_date_str or not return_date_str:
            return JsonResponse({'available': True})
        pickup_date = parse_br_date(pickup_date_str)
        return_date = parse_br_date(return_date_str)
        if pickup_date is None or return_date is None:
            return JsonResponse({'available': False, 'error': 'invalid_date'})
        try:
            val = int(product_id)
            if val > 2147483647:
                return JsonResponse({'available': False, 'error': 'not_found'})
            product = Product.objects.select_related('category').get(pk=val)
        except (Product.DoesNotExist, ValueError):
            return JsonResponse({'available': False, 'error': 'not_found'})
        rental = find_overlapping_rental(product, pickup_date, return_date)
        if rental:
            return JsonResponse({
                'available': False,
                'rental_number': rental.number,
                'customer': rental.customer.name,
                'pickup_date': rental.pickup_date.isoformat(),
                'return_date': rental.return_date.isoformat(),
            })
        return JsonResponse({'available': True})

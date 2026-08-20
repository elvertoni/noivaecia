import re
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from core.mixins import ModuleAccessMixin, ActionRequiredMixin
from core.models import AuditLog
from core.ui import parse_br_date

from rentals.models import Rental, RentalItem
from customers.models import _normalize_name

from .availability import (
    INACTIVE_RENTAL_STATUSES,
    find_overlapping_rental,
    find_relevant_rentals,
)
from .forms import AvailabilityQueryForm, CategoryForm, CategoryMergeForm, ProductForm
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
        qs = super().get_queryset().annotate(
            product_count=Count(
                'products',
                filter=Q(products__is_active=True),
            )
        )
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
        category = self.object
        try:
            with transaction.atomic():
                response = super().form_valid(form)
                AuditLog.objects.create(
                    user=self.request.user,
                    action='category_delete',
                    model_name='Category',
                    object_id=str(category.pk),
                    object_repr=str(category),
                    reason='Exclusão de categoria.',
                )
        except ProtectedError:
            messages.error(
                self.request,
                'Esta categoria não pode ser excluída porque possui produtos vinculados.',
            )
            return redirect('catalog:category_list')
        messages.success(self.request, 'Categoria excluída com sucesso.')
        return response


# ── Products ──────────────────────────────────────────────────────────────────

class ProductListView(CatalogAccessMixin, ListView):
    """Product listing with extended filters (R8.01/R8.02)."""

    model = Product
    template_name = 'catalog/product_list.html'
    context_object_name = 'products'
    paginate_by = 30

    def get_queryset(self):
        status = self.request.GET.get('status', 'active').strip()
        if status not in {'active', 'inactive', 'all'}:
            status = 'active'

        duplicate = Product.objects.filter(
            category_id=OuterRef('category_id'),
            code=OuterRef('code'),
        ).exclude(pk=OuterRef('pk'))
        if status == 'active':
            duplicate = duplicate.filter(is_active=True)
        elif status == 'inactive':
            duplicate = duplicate.filter(is_active=False)

        qs = (
            super().get_queryset()
            .select_related('category')
            .annotate(is_duplicate=Exists(duplicate))
        )
        if status == 'active':
            qs = qs.filter(is_active=True)
        elif status == 'inactive':
            qs = qs.filter(is_active=False)

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
                if not 0 <= val <= 2147483647:
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
        status = self.request.GET.get('status', 'active').strip()
        if status not in {'active', 'inactive', 'all'}:
            status = 'active'
        status_scope = Product.objects.all()
        if status == 'active':
            status_scope = status_scope.filter(is_active=True)
        elif status == 'inactive':
            status_scope = status_scope.filter(is_active=False)
        ctx.update({
            'prefix': self.request.GET.get('prefix', ''),
            'code': self.request.GET.get('code', ''),
            'description': self.request.GET.get('description', ''),
            'color': self.request.GET.get('color', ''),
            'size': self.request.GET.get('size', ''),
            'only_placeholder': self.request.GET.get('placeholder', ''),
            'only_duplicate': self.request.GET.get('duplicate', ''),
            'status': status,
            'categories': Category.objects.all(),
            'placeholder_count': status_scope.filter(is_placeholder=True).count(),
            'inactive_count': Product.objects.filter(is_active=False).count(),
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['has_related_rentals'] = self.object.rental_items.exists()
        return context

    def form_valid(self, form):
        with transaction.atomic():
            product = get_object_or_404(
                Product.objects.select_for_update().select_related('category'),
                pk=self.object.pk,
            )
            product_id = product.pk
            product_repr = str(product)[:200]

            if product.rental_items.exists():
                archived = self._archive_product(product, product_repr)
            else:
                try:
                    # The savepoint keeps the outer transaction usable when a
                    # rental item is attached between the existence check and
                    # the delete attempt.
                    with transaction.atomic():
                        product.delete()
                except ProtectedError:
                    product = Product.objects.select_for_update().get(pk=product_id)
                    archived = self._archive_product(product, product_repr)
                else:
                    AuditLog.objects.create(
                        user=self.request.user,
                        action='product_delete',
                        model_name='Product',
                        object_id=str(product_id),
                        object_repr=product_repr,
                        reason='Exclusão física de produto sem histórico de locações.',
                    )
                    messages.success(self.request, 'Produto excluído com sucesso.')
                    return redirect(self.success_url)

        if archived:
            messages.success(
                self.request,
                'Produto retirado do acervo com sucesso. O histórico de locações foi preservado.',
            )
        else:
            messages.info(self.request, 'Este produto já estava fora do acervo.')
        return redirect(self.success_url)

    def _archive_product(self, product, product_repr):
        if not product.is_active:
            return False
        product.is_active = False
        product.save(update_fields=['is_active', 'updated_at'])
        AuditLog.objects.create(
            user=self.request.user,
            action='product_archive',
            model_name='Product',
            object_id=str(product.pk),
            object_repr=product_repr,
            reason='Retirada do acervo; histórico de locações preservado.',
            metadata={'is_active': {'from': True, 'to': False}},
        )
        return True


class ProductReactivateView(CatalogAccessMixin, ActionRequiredMixin, View):
    action_key = 'catalog.delete'

    def post(self, request, pk):
        with transaction.atomic():
            product = get_object_or_404(
                Product.objects.select_for_update().select_related('category'),
                pk=pk,
            )
            if product.is_active:
                messages.info(request, 'Este produto já está ativo no acervo.')
                return redirect('catalog:product_list')

            product_repr = str(product)[:200]
            product.is_active = True
            product.save(update_fields=['is_active', 'updated_at'])
            AuditLog.objects.create(
                user=request.user,
                action='product_reactivate',
                model_name='Product',
                object_id=str(product.pk),
                object_repr=product_repr,
                reason='Produto reativado no acervo.',
                metadata={'is_active': {'from': False, 'to': True}},
            )

        messages.success(request, 'Produto reativado no acervo com sucesso.')
        return redirect('catalog:product_list')


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
    """Operational prefix/code lookup with an optional reference date."""

    template_name = 'catalog/availability.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = AvailabilityQueryForm(self.request.GET or None)
        product_id = self.request.GET.get('product_id', '').strip()

        context['form'] = form
        context['prefix'] = self.request.GET.get('prefix', '').strip()
        context['code'] = self.request.GET.get('code', '').strip()
        context['date'] = self.request.GET.get('date', '').strip()

        if not form.is_bound:
            return context

        if not form.is_valid():
            context['error'] = 'Revise os campos indicados para consultar o produto.'
            return context

        prefix = form.cleaned_data['prefix']
        code = form.cleaned_data['code']
        requested_date = form.cleaned_data['date']
        reference_date = requested_date or timezone.localdate()
        context['prefix'] = prefix
        context['reference_date'] = reference_date
        context['uses_custom_date'] = requested_date is not None

        products = list(
            Product.objects.filter(
                category__prefix__iexact=prefix,
                code=code,
                is_active=True,
            )
            .select_related('category')
            .order_by('is_placeholder', 'category__prefix', 'code', 'pk')
        )
        if not products:
            context['error'] = (
                f'Produto {prefix}{code} não encontrado. Confira o prefixo e o código.'
            )
            return context

        # R8.03 — disambiguation when duplicates exist
        if len(products) > 1:
            context['needs_disambiguation'] = True
            context['candidates'] = products
            if not product_id:
                return context
            product = next((p for p in products if str(p.pk) == product_id), None)
            if product is None:
                context['error'] = 'Selecione um dos produtos encontrados para continuar.'
                return context
            context['needs_disambiguation'] = False
        else:
            product = products[0]

        scheduled_rentals = list(find_relevant_rentals(product, reference_date))
        rentals_on_date = [
            rental for rental in scheduled_rentals
            if (
                (
                    rental.status == Rental.Status.PICKED_UP
                    and reference_date >= rental.pickup_date
                )
                or rental.pickup_date <= reference_date <= rental.return_date
            )
        ]

        context['product'] = product
        context['checked'] = True
        context['rental'] = min(
            rentals_on_date,
            key=lambda rental: (rental.return_date, rental.number),
            default=None,
        )
        context['scheduled_rentals'] = scheduled_rentals
        return context


# ── Placeholder review (R8.05) ────────────────────────────────────────────────

class PlaceholderReviewView(CatalogAccessMixin, TemplateView):
    """List placeholder categories and products for admin review (R8.05)."""

    template_name = 'catalog/placeholder_review.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['placeholder_categories'] = (
            Category.objects.filter(is_placeholder=True)
            .annotate(
                product_count=Count(
                    'products',
                    filter=Q(products__is_active=True),
                )
            )
            .order_by('prefix')
        )
        ctx['placeholder_products'] = (
            Product.objects.filter(is_placeholder=True, is_active=True)
            .select_related('category')
            .order_by('category__prefix', 'code')
        )
        return ctx


# ── Category merge (R8.06) ────────────────────────────────────────────────────

class CategoryMergeView(CatalogAccessMixin, ActionRequiredMixin, View):
    """Merge source category into target, updating all products/items (R8.06).

    GET/POST preview=1: show impact preview.
    POST confirmed=1: execute in atomic transaction.
    """

    action_key = 'catalog.delete'
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

        # Count rental items affected via products in source category
        from rentals.models import RentalItem
        product_count = Product.objects.filter(category=source).count()
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
            categories = {
                category.pk: category
                for category in Category.objects.select_for_update().filter(
                    pk__in=(source.pk, target.pk),
                )
            }
            if len(categories) != 2:
                messages.error(
                    request,
                    'Uma das categorias foi alterada ou excluída. Revise a mesclagem.',
                )
                return redirect('catalog:category_merge')
            source = categories[source.pk]
            target = categories[target.pk]
            product_count = Product.objects.filter(category=source).count()
            item_count = RentalItem.objects.filter(product__category=source).count()
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
            .filter(is_active=True)
            .filter(product_text_filter(q))
            .order_by('category__prefix', 'code')[:20]
        )
        results = [
            {
                'id': p.pk,
                'code': f'{p.category.prefix}{p.code}',
                'text': p.description,
                'sub': f'{p.color or "—"} · {p.size or "—"}',
                'color': p.color,
                'size': p.size,
                'value': str(p.value),
            }
            for p in qs
        ]
        return JsonResponse({'results': results})


class InactiveProductCodesView(CatalogAccessMixin, View):
    """List reusable codes from archived or legacy-null inventory items."""

    def get(self, request, *args, **kwargs):
        category_id = request.GET.get('category', '').strip()
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            return JsonResponse({'results': []})

        category = Category.objects.filter(pk=category_id).first()
        if category is None:
            return JsonResponse({'results': []})

        codes_in_use = Product.objects.filter(
            category=category, is_active=True,
        ).exclude(description__iexact='nulo').values('code')

        codes = (
            Product.objects.filter(category=category)
            .filter(Q(is_active=False) | Q(description__iexact='nulo'))
            .exclude(code__in=codes_in_use)
            .values('code')
            .annotate(records=Count('pk'))
            .order_by('code')
        )
        return JsonResponse({
            'results': [
                {
                    'code': row['code'],
                    'label': f'{category.prefix}{row["code"]}',
                    'records': row['records'],
                }
                for row in codes
            ],
        })


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
            if (
                pickup_date is None
                or return_date is None
                or return_date < pickup_date
            ):
                return JsonResponse(
                    {'results': [], 'error': 'invalid_date'},
                    status=400,
                )

        exclude_rental_id = self._parse_rental_id(
            request.GET.get('exclude_rental_id', ''),
        )

        # ``scoped``: q + visibility only (drives the category facet).
        scoped = Product.objects.select_related('category').filter(is_active=True)
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
            if exclude_rental_id is not None:
                active_item = active_item.exclude(rental_id=exclude_rental_id)
            results_qs = results_qs.annotate(in_use=Exists(active_item))

        total = results_qs.count()
        num_pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = min(page, num_pages)
        start = (page - 1) * self.PAGE_SIZE
        page_items = list(results_qs[start:start + self.PAGE_SIZE])

        rental_map = (
            self._rentals_for_page(
                page_items,
                pickup_date,
                return_date,
                exclude_rental_id=exclude_rental_id,
            )
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

    @staticmethod
    def _parse_rental_id(value):
        try:
            rental_id = int(value)
        except (TypeError, ValueError):
            return None
        if 1 <= rental_id <= 2147483647:
            return rental_id
        return None

    def _rentals_for_page(
        self,
        page_items,
        pickup_date,
        return_date,
        exclude_rental_id=None,
    ):
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
        if exclude_rental_id is not None:
            items = items.exclude(rental_id=exclude_rental_id)
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
        if not product_id:
            return JsonResponse({'available': True})
        try:
            val = int(product_id)
            if not 1 <= val <= 2147483647:
                return JsonResponse({'available': False, 'error': 'not_found'})
            product = Product.objects.select_related('category').get(pk=val, is_active=True)
        except (Product.DoesNotExist, ValueError):
            return JsonResponse({'available': False, 'error': 'not_found'})
        if not pickup_date_str or not return_date_str:
            return JsonResponse({'available': True})
        pickup_date = parse_br_date(pickup_date_str)
        return_date = parse_br_date(return_date_str)
        if (
            pickup_date is None
            or return_date is None
            or return_date < pickup_date
        ):
            return JsonResponse({'available': False, 'error': 'invalid_date'})
        exclude_rental_id = ProductBrowseView._parse_rental_id(
            request.GET.get('exclude_rental_id', ''),
        )
        rental = find_overlapping_rental(
            product,
            pickup_date,
            return_date,
            exclude_rental_id=exclude_rental_id,
        )
        if rental:
            return JsonResponse({
                'available': False,
                'rental_number': rental.number,
                'customer': rental.customer.name,
                'pickup_date': rental.pickup_date.isoformat(),
                'return_date': rental.return_date.isoformat(),
            })
        return JsonResponse({'available': True})

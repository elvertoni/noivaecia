import re
from datetime import date as date_cls

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

from .availability import find_rental_for
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
        return qs

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
                qs = qs.filter(code=int(code))
            except ValueError:
                qs = qs.none()
        if description:
            qs = qs.filter(description__icontains=description)
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

        try:
            on_date = date_cls.fromisoformat(date_str)
        except ValueError:
            context['error'] = 'Data inválida.'
            return context

        products = list(
            Product.objects.filter(category__prefix__iexact=prefix, code=code)
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
        form = CategoryMergeForm()
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

class ProductSearchView(View):
    """JSON quick-search for product picker in rental item form (R7.03)."""

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'results': []}, status=403)
        q = request.GET.get('q', '').strip()
        code_match = re.match(r'^([A-Za-z]+)?\s*0*(\d+)$', q)
        if len(q) < 2 and not code_match:
            return JsonResponse({'results': []})
        q_filter = (
            Q(description__icontains=q)
            | Q(color__icontains=q)
            | Q(size__icontains=q)
            | Q(category__prefix__icontains=q)
        )
        if code_match:
            prefix, code = code_match.groups()
            code_filter = Q(code=int(code))
            if prefix:
                code_filter &= Q(category__prefix__iexact=prefix)
            q_filter |= code_filter
        qs = (
            Product.objects.select_related('category')
            .filter(q_filter)
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


class ProductAvailabilityJsonView(View):
    """JSON availability check for a product on a given pickup date (R7.04)."""

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'available': False, 'error': 'auth'}, status=403)
        product_id = request.GET.get('product_id', '').strip()
        date_str = request.GET.get('date', '').strip()
        if not product_id or not date_str:
            return JsonResponse({'available': True})
        try:
            on_date = date_cls.fromisoformat(date_str)
        except ValueError:
            return JsonResponse({'available': True})
        try:
            product = Product.objects.select_related('category').get(pk=int(product_id))
        except (Product.DoesNotExist, ValueError):
            return JsonResponse({'available': False, 'error': 'not_found'})
        rental = find_rental_for(product, on_date)
        if rental:
            return JsonResponse({
                'available': False,
                'rental_number': rental.number,
                'customer': rental.customer.name,
                'return_date': rental.return_date.isoformat(),
            })
        return JsonResponse({'available': True})

from datetime import date as date_cls

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from core.mixins import ModuleAccessMixin

from .availability import find_rental_for
from .forms import CategoryForm, ProductForm
from .models import Category, Product


class CatalogAccessMixin(ModuleAccessMixin):
    module_key = 'catalog'


# --- Categories (RF-12) ---

class CategoryListView(CatalogAccessMixin, ListView):
    model = Category
    template_name = 'catalog/category_list.html'
    context_object_name = 'categories'
    paginate_by = 20


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


class CategoryDeleteView(CatalogAccessMixin, DeleteView):
    model = Category
    template_name = 'catalog/category_confirm_delete.html'
    success_url = reverse_lazy('catalog:category_list')

    def form_valid(self, form):
        messages.success(self.request, 'Categoria excluída com sucesso.')
        return super().form_valid(form)


# --- Products (RF-13) ---

class ProductListView(CatalogAccessMixin, ListView):
    model = Product
    template_name = 'catalog/product_list.html'
    context_object_name = 'products'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset().select_related('category')
        prefix = self.request.GET.get('prefix', '').strip()
        if prefix:
            queryset = queryset.filter(category__prefix__iexact=prefix)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['prefix'] = self.request.GET.get('prefix', '')
        context['categories'] = Category.objects.all()
        return context


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


class ProductDeleteView(CatalogAccessMixin, DeleteView):
    model = Product
    template_name = 'catalog/product_confirm_delete.html'
    success_url = reverse_lazy('catalog:product_list')

    def form_valid(self, form):
        messages.success(self.request, 'Produto excluído com sucesso.')
        return super().form_valid(form)


# --- Availability lookup (RF-22) ---

class AvailabilityView(CatalogAccessMixin, TemplateView):
    """Check whether a product is available or rented on a date (RF-22)."""

    template_name = 'catalog/availability.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prefix = self.request.GET.get('prefix', '').strip()
        code = self.request.GET.get('code', '').strip()
        date_str = self.request.GET.get('date', '').strip()

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

        product = (
            Product.objects.filter(category__prefix__iexact=prefix, code=code)
            .select_related('category')
            .first()
        )
        if product is None:
            context['error'] = 'Produto não encontrado.'
            return context

        context['product'] = product
        context['checked'] = True
        context['rental'] = find_rental_for(product, on_date)
        return context

from django.urls import path

from .views import (
    AvailabilityView,
    CategoryCreateView,
    CategoryDeleteView,
    CategoryListView,
    CategoryUpdateView,
    ProductCreateView,
    ProductDeleteView,
    ProductListView,
    ProductUpdateView,
)

app_name = 'catalog'

urlpatterns = [
    # Categories
    path('categorias/', CategoryListView.as_view(), name='category_list'),
    path('categorias/nova/', CategoryCreateView.as_view(), name='category_create'),
    path('categorias/<int:pk>/editar/', CategoryUpdateView.as_view(), name='category_update'),
    path('categorias/<int:pk>/excluir/', CategoryDeleteView.as_view(), name='category_delete'),
    # Products
    path('produtos/', ProductListView.as_view(), name='product_list'),
    path('produtos/novo/', ProductCreateView.as_view(), name='product_create'),
    path('produtos/<int:pk>/editar/', ProductUpdateView.as_view(), name='product_update'),
    path('produtos/<int:pk>/excluir/', ProductDeleteView.as_view(), name='product_delete'),
    # Availability (RF-22)
    path('disponibilidade/', AvailabilityView.as_view(), name='availability'),
]

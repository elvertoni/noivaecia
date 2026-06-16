from django.urls import path

from .views import (
    AvailabilityView,
    CategoryCreateView,
    CategoryDeleteView,
    CategoryListView,
    CategoryMergeView,
    CategoryUpdateView,
    PlaceholderReviewView,
    ProductAvailabilityJsonView,
    ProductCreateView,
    ProductDeleteView,
    ProductBrowseView,
    ProductHistoryView,
    ProductListView,
    ProductSearchView,
    ProductUpdateView,
)

app_name = 'catalog'

urlpatterns = [
    # Categories
    path('categorias/', CategoryListView.as_view(), name='category_list'),
    path('categorias/nova/', CategoryCreateView.as_view(), name='category_create'),
    path('categorias/mesclar/', CategoryMergeView.as_view(), name='category_merge'),
    path('categorias/<int:pk>/editar/', CategoryUpdateView.as_view(), name='category_update'),
    path('categorias/<int:pk>/excluir/', CategoryDeleteView.as_view(), name='category_delete'),
    # Products
    path('produtos/', ProductListView.as_view(), name='product_list'),
    path('produtos/novo/', ProductCreateView.as_view(), name='product_create'),
    path('produtos/placeholders/', PlaceholderReviewView.as_view(), name='placeholder_review'),
    path('produtos/<int:pk>/editar/', ProductUpdateView.as_view(), name='product_update'),
    path('produtos/<int:pk>/excluir/', ProductDeleteView.as_view(), name='product_delete'),
    path('produtos/<int:pk>/historico/', ProductHistoryView.as_view(), name='product_history'),
    # Availability (RF-22)
    path('disponibilidade/', AvailabilityView.as_view(), name='availability'),
    # JSON API for rental form (R7.03/R7.04)
    path('api/busca/', ProductSearchView.as_view(), name='product_search'),
    path('api/navegar/', ProductBrowseView.as_view(), name='product_browse'),
    path('api/disponibilidade/', ProductAvailabilityJsonView.as_view(), name='availability_json'),
]

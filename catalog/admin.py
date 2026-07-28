from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'name')
    search_fields = ('prefix', 'name')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('category', 'code', 'description', 'color', 'size', 'value', 'is_active')
    list_filter = ('is_active', 'category')
    readonly_fields = ('is_active',)
    search_fields = ('description', 'code')

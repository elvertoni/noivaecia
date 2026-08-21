from django.contrib import admin

from .forms import CategoryForm, ProductForm
from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    # The default ModelForm skips ``clean_prefix``, so the admin could create
    # ``vf`` next to ``VF`` — two categories that read as one everywhere the
    # lookup matches ``prefix__iexact``, each able to hold its own live code.
    form = CategoryForm
    list_display = ('prefix', 'name')
    search_fields = ('prefix', 'name')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    # A UniqueConstraint whose ``condition`` names a field the form excludes is
    # skipped silently during validation (``constraints.py``:
    # ``except FieldError: pass``), and ModelForm excludes everything outside
    # ``Meta.fields`` — ``is_active`` included.  Without ``ProductForm`` the
    # admin would validate a duplicate code as clean and then raise
    # IntegrityError on save, as a 500.
    form = ProductForm
    list_display = ('category', 'code', 'description', 'color', 'size', 'value', 'is_active')
    list_filter = ('is_active', 'category')
    readonly_fields = ('is_active', 'legacy_id', 'legacy_source', 'legacy_notes')
    search_fields = ('description', 'code')

    def has_delete_permission(self, request, obj=None):
        """Retiring a product is always in place — the row never goes away.

        Deleting frees the ``(category, code)`` slot with no record that the
        code ever existed, which is the exact hole ``ProductDeleteView`` was
        rewritten to close.  Use "Retirar do acervo" instead; the code then
        stays available for reuse and the history keeps its anchor.
        """
        return False

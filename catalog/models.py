from django.db import models
from django.db.models.functions import Upper
from django.urls import reverse

from core.models import TimeStampedModel


class Category(TimeStampedModel):
    """Product category identified by a short unique prefix (RF-12)."""

    prefix = models.CharField('prefixo', max_length=10, unique=True)
    name = models.CharField('nome', max_length=100)
    # R3.01 / R3.02
    legacy_id = models.PositiveIntegerField('ID legado', null=True, blank=True, db_index=True)
    legacy_source = models.CharField('origem legada', max_length=50, blank=True)
    legacy_notes = models.TextField('notas de importação', blank=True)
    is_placeholder = models.BooleanField('é placeholder', default=False, db_index=True)

    class Meta:
        verbose_name = 'categoria'
        verbose_name_plural = 'categorias'
        ordering = ('prefix',)
        constraints = [
            # ``prefix`` is already unique, but uniqueness in PostgreSQL is
            # case-sensitive while availability and product lookup match it with
            # ``prefix__iexact``.  ``VF`` and ``vf`` therefore read as one
            # category on screen and are two in the database — and each may hold
            # its own live code 731, putting "VF731" on screen twice.  That is
            # the duplication the catalogue is being cleaned up for, so the rule
            # cannot live only in ``CategoryForm.clean_prefix``: the admin, the
            # shell and data migrations all reach ``Category`` without it.
            models.UniqueConstraint(
                Upper('prefix'),
                name='catalog_category_prefix_unique_ci',
                violation_error_message='Já existe uma categoria com este prefixo.',
            ),
        ]

    def __str__(self):
        return f'{self.prefix} · {self.name}'

    def get_absolute_url(self):
        return reverse('catalog:category_update', args=[self.pk])


class Product(TimeStampedModel):
    """Inventory item belonging to a category (RF-13)."""

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products',
        verbose_name='categoria',
    )
    code = models.PositiveIntegerField('código')
    description = models.CharField('descrição', max_length=200)
    description_search = models.CharField('descrição normalizada', max_length=220, blank=True)
    color = models.CharField('cor', max_length=50, blank=True)
    size = models.CharField('tamanho', max_length=50, blank=True)
    value = models.DecimalField('valor', max_digits=10, decimal_places=2, default=0)
    notes = models.TextField('observações', blank=True)
    # R3.01 / R3.02
    legacy_id = models.PositiveIntegerField('ID legado', null=True, blank=True, db_index=True)
    legacy_source = models.CharField('origem legada', max_length=50, blank=True)
    legacy_notes = models.TextField('notas de importação', blank=True)
    is_placeholder = models.BooleanField('é placeholder', default=False, db_index=True)
    is_active = models.BooleanField('ativo', default=True, db_index=True)

    class Meta:
        verbose_name = 'produto'
        verbose_name_plural = 'produtos'
        ordering = ('category__prefix', 'code')
        indexes = [
            models.Index(fields=('category', 'code'), name='catalog_product_lookup_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gte=0),
                name='catalog_product_value_gte_0',
            ),
            # The code is the piece's identity in this business — it goes on the
            # printed contract and on the physical tag — so it must mean exactly
            # one item in the collection.  Form validation is check-then-insert
            # and loses the race; the admin, the MCP tools and the importer skip
            # the form entirely.  Only the index arbitrates.  The condition lets
            # many retired rows share a code, which is how the legacy system
            # parked freed slots and how reuse revives them.
            models.UniqueConstraint(
                fields=('category', 'code'),
                condition=models.Q(is_active=True),
                name='catalog_product_unique_active_code',
                violation_error_message=(
                    'Este código já está no acervo desta categoria. Retire o item '
                    'atual antes de reaproveitar o código.'
                ),
            ),
        ]

    def __str__(self):
        return f'{self.category.prefix}{self.code} · {self.description}'

    def save(self, *args, **kwargs):
        import unicodedata
        value = self.description or ''
        nfkd = unicodedata.normalize('NFKD', value)
        stripped = ''.join(c for c in nfkd if not unicodedata.combining(c))
        self.description_search = ' '.join(stripped.lower().split())
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('catalog:product_update', args=[self.pk])

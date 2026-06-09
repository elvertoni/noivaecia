from django.db import models
from django.urls import reverse

from core.models import TimeStampedModel


class Category(TimeStampedModel):
    """Product category identified by a short unique prefix (RF-12)."""

    prefix = models.CharField('prefixo', max_length=10, unique=True)
    name = models.CharField('nome', max_length=100)

    class Meta:
        verbose_name = 'categoria'
        verbose_name_plural = 'categorias'
        ordering = ('prefix',)

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
    color = models.CharField('cor', max_length=50, blank=True)
    size = models.CharField('tamanho', max_length=50, blank=True)
    value = models.DecimalField('valor', max_digits=10, decimal_places=2, default=0)
    notes = models.TextField('observações', blank=True)

    class Meta:
        verbose_name = 'produto'
        verbose_name_plural = 'produtos'
        ordering = ('category__prefix', 'code')
        unique_together = ('category', 'code')

    def __str__(self):
        return f'{self.category.prefix}{self.code} · {self.description}'

    def get_absolute_url(self):
        return reverse('catalog:product_update', args=[self.pk])

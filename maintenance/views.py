from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.views.generic import TemplateView, View

from billing.models import Receivable
from core.mixins import ModuleAccessMixin
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental

from catalog.models import Category, Product


class MaintenanceAccessMixin(ModuleAccessMixin):
    module_key = 'maintenance'


class MaintenanceView(MaintenanceAccessMixin, TemplateView):
    """Restricted maintenance area with controlled DB routines (RF-25)."""

    template_name = 'maintenance/maintenance.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['stats'] = {
            'customers': Customer.objects.count(),
            'categories': Category.objects.count(),
            'products': Product.objects.count(),
            'rentals': Rental.objects.count(),
            'receivables': Receivable.objects.count(),
        }
        return context


class RecalculateRentalTotalsView(MaintenanceAccessMixin, View):
    """Recompute every rental's total from its items (controlled routine)."""

    def post(self, request, *args, **kwargs):
        count = 0
        for rental in Rental.objects.prefetch_related('items').all():
            rental.recalculate_total()
            count += 1
        messages.success(request, f'Totais recalculados para {count} locação(ões).')
        return redirect('maintenance:index')


class RecalculateBalancesView(MaintenanceAccessMixin, View):
    """Recalculate receivable balances from Payment records with preview (R6.07).

    GET: preview — show count without changing data.
    POST: execute in a transaction, log to AuditLog.
    """

    def get(self, request, *args, **kwargs):
        count = Receivable.objects.count()
        messages.info(
            request,
            f'Pré-visualização: {count} recebimento(s) serão recalculados. '
            'Use o botão "Executar" para confirmar.'
        )
        return redirect('maintenance:index')

    def post(self, request, *args, **kwargs):
        from django.db import transaction
        from core.models import AuditLog

        with transaction.atomic():
            count = 0
            for receivable in Receivable.objects.prefetch_related('payments').all():
                receivable.recalculate_from_payments(save=True)
                count += 1

        AuditLog.objects.create(
            user=request.user,
            action='recalculate_balances',
            model_name='Receivable',
            object_id='all',
            object_repr=f'{count} recebimentos',
            reason=f'Recálculo manual de saldos via manutenção.',
        )
        messages.success(request, f'Saldos recalculados para {count} recebimento(s).')
        return redirect('maintenance:index')


class SettleReturnsView(MaintenanceAccessMixin, TemplateView):
    """Report inconsistencies in return/pickup records (R10.07 — Acerto de devoluções)."""

    template_name = 'maintenance/settle_returns.html'

    def get_context_data(self, **kwargs):
        from movements.models import Pickup, Return
        ctx = super().get_context_data(**kwargs)

        # 1. Rental.status='returned' without a Return record
        returned_no_record = list(
            Rental.objects.filter(status=Rental.Status.RETURNED)
            .exclude(pk__in=Return.objects.values('rental_id'))
            .select_related('customer')
            .order_by('-number')
        )

        # 2. Return records where Rental.status != 'returned'
        return_wrong_status = list(
            Return.objects.exclude(rental__status=Rental.Status.RETURNED)
            .select_related('rental', 'rental__customer')
            .order_by('-rental__number')
        )

        # 3. Rental.status in (picked_up, returned) without a Pickup record
        picked_no_pickup = list(
            Rental.objects.filter(status__in=[Rental.Status.PICKED_UP, Rental.Status.RETURNED])
            .exclude(pk__in=Pickup.objects.values('rental_id'))
            .select_related('customer')
            .order_by('-number')
        )

        # 4. Return.return_date < Pickup.pickup_date (impossible dates)
        impossible_dates = list(
            Return.objects.select_related('rental', 'rental__customer', 'rental__pickup')
            .filter(rental__pickup__isnull=False)
            .order_by('-rental__number')
        )
        impossible_dates = [r for r in impossible_dates if r.return_date < r.rental.pickup.pickup_date]

        ctx.update({
            'returned_no_record': returned_no_record,
            'return_wrong_status': return_wrong_status,
            'picked_no_pickup': picked_no_pickup,
            'impossible_dates': impossible_dates,
            'total_issues': (
                len(returned_no_record) + len(return_wrong_status)
                + len(picked_no_pickup) + len(impossible_dates)
            ),
        })
        return ctx


class ImportQualityView(MaintenanceAccessMixin, TemplateView):
    """Import quality dashboard — placeholders, duplicates, suspicious dates (R12.09)."""

    template_name = 'maintenance/import_quality.html'

    def get_context_data(self, **kwargs):
        from django.db.models import Count, Q
        from billing.models import FinancialMovement
        ctx = super().get_context_data(**kwargs)

        ctx['placeholder_categories'] = Category.objects.filter(is_placeholder=True).count()
        ctx['placeholder_products'] = Product.objects.filter(is_placeholder=True).count()
        ctx['placeholder_customers'] = Customer.objects.filter(is_placeholder=True).count()

        dup_pairs = (
            Product.objects.values('category_id', 'code')
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
            .count()
        )
        ctx['duplicate_product_pairs'] = dup_pairs

        ctx['suspicious_date_rentals'] = Rental.objects.filter(
            pickup_date__lt='2000-01-01'
        ).count()

        ctx['rentals_no_items'] = (
            Rental.objects.annotate(item_count=Count('items'))
            .filter(item_count=0)
            .count()
        )

        ctx['orphan_movements'] = FinancialMovement.objects.filter(
            receivable__isnull=True,
            source=FinancialMovement.Source.IMPORT,
        ).count()

        return ctx


class LegacyAuditView(MaintenanceAccessMixin, TemplateView):
    """Show legacy programas/libera tables as read-only audit reference (R12.07)."""

    template_name = 'maintenance/legacy_audit.html'

    def get_context_data(self, **kwargs):
        from django.db import connection
        ctx = super().get_context_data(**kwargs)

        def _query(table):
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT * FROM {table} ORDER BY 1')
                    cols = [col[0] for col in cursor.description]
                    return [dict(zip(cols, row)) for row in cursor.fetchall()]
            except Exception:
                return []

        ctx['programas'] = _query('legacy_programas')
        ctx['libera'] = _query('legacy_libera')
        return ctx


class ReconcileFinancialView(MaintenanceAccessMixin, TemplateView):
    """Run financial reconciliation report (R12.10 / R6.05)."""

    template_name = 'maintenance/reconcile.html'

    def get(self, request, *args, **kwargs):
        from billing.services import reconcile_financial
        ctx = reconcile_financial()
        messages.info(
            request,
            f'Reconciliação: {ctx.get("total_divergences", 0)} divergência(s) encontrada(s).'
        )
        return self.render_to_response(self.get_context_data(**ctx))

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs)

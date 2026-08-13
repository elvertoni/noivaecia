"""Shared UI component guards: row action menus, confirm dialog, dashboard.

These cover the pieces every screen inherits from `base.html` and the design
system, so a regression shows up once here instead of once per module.
"""

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActionPermission, ModulePermission
from catalog.models import Category, Product
from customers.models import Customer
from rentals.models import Rental


User = get_user_model()


def grant(user, modules=(), actions=()):
    for key in modules:
        ModulePermission.objects.create(user=user, module_key=key, allowed=True)
    for key in actions:
        ActionPermission.objects.create(user=user, action_key=key, allowed=True)


def action_cells(html):
    """Return the markup of each row's actions cell (the last <td> of a row)."""
    return re.findall(r'<td class="text-right[^"]*">(.*?)</td>', html, re.DOTALL)


class ConfirmDialogTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='confirm-dialog@noivasecia.test', password='Senha12345',
        )
        grant(user, modules=['customers'])
        self.client.force_login(user)

    def test_every_authenticated_page_ships_the_shared_dialog(self):
        html = self.client.get(reverse('customers:list')).content.decode()

        self.assertIn('id="confirm-dialog"', html)
        self.assertIn('role="alertdialog"', html)
        self.assertIn('data-confirm-accept', html)
        self.assertIn('data-confirm-dismiss', html)

    def test_no_screen_falls_back_to_the_native_confirm(self):
        """`window.confirm` is unstyled, untranslatable and blocks the thread."""
        for url in (reverse('customers:list'), reverse('dashboard')):
            html = self.client.get(url).content.decode()
            self.assertNotIn('return confirm(', html, msg=f'{url} usa confirm() nativo')


class RowActionMenuTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='row-actions@noivasecia.test', password='Senha12345',
        )
        grant(
            user,
            modules=['catalog', 'customers'],
            actions=['catalog.delete'],
        )
        self.client.force_login(user)
        category = Category.objects.create(prefix='VF', name='Vestido de festa')
        Product.objects.create(
            category=category, code=38, description='VESTIDO LONGO',
            color='ROSE', size='M', value=Decimal('380.00'),
        )
        Customer.objects.create(name='Cliente do Menu')

    def test_product_row_actions_collapse_into_one_menu(self):
        html = self.client.get(reverse('catalog:product_list')).content.decode()
        cell = [c for c in action_cells(html) if 'action-menu' in c][0]

        self.assertIn('aria-haspopup="menu"', cell)
        self.assertIn('aria-expanded="false"', cell)
        self.assertEqual(cell.count('class="action-menu-panel"'), 1)
        for label in ('Histórico', 'Editar', 'Retirar do acervo'):
            self.assertIn(f'>{label}</a>', cell)

    def test_destructive_product_action_is_marked_as_such(self):
        html = self.client.get(reverse('catalog:product_list')).content.decode()

        self.assertIn('action-menu-item action-menu-item-danger', html)

    def test_customer_row_actions_collapse_into_one_menu(self):
        html = self.client.get(reverse('customers:list')).content.decode()
        cell = [c for c in action_cells(html) if 'action-menu' in c][0]

        self.assertEqual(cell.count('class="action-menu-panel"'), 1)
        self.assertIn('>Histórico</a>', cell)
        self.assertIn('>Editar</a>', cell)

    def test_menu_trigger_names_the_row_it_belongs_to(self):
        """Otherwise a screen reader announces a page of identical "Ações"."""
        html = self.client.get(reverse('catalog:product_list')).content.decode()

        self.assertIn('Mais ações do produto: VF38 · VESTIDO LONGO', html)


class DashboardComponentTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='dashboard-ui@noivasecia.test', password='Senha12345',
        )
        grant(user, modules=['movements', 'rentals', 'catalog'])
        self.client.force_login(user)
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(name='Cliente de Hoje')

    def test_module_cards_carry_an_icon_per_module(self):
        html = self.client.get(reverse('dashboard')).content.decode()

        self.assertIn('class="module-card"', html)
        self.assertEqual(html.count('module-card-icon'), html.count('class="module-card"'))

    def test_today_summary_lists_pickups_and_returns_due_today(self):
        due_today = Rental.objects.create(
            customer=self.customer,
            number=9001,
            pickup_date=self.today,
            return_date=self.today,
            status=Rental.Status.PENDING,
        )
        picked_up = Rental.objects.create(
            customer=self.customer,
            number=9002,
            pickup_date=self.today,
            return_date=self.today,
            status=Rental.Status.PICKED_UP,
        )

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Resumo de hoje')
        self.assertEqual(list(response.context['today_pickups']), [due_today])
        self.assertEqual(list(response.context['today_returns']), [picked_up])

    def test_today_summary_excludes_legacy_payment_only_rentals(self):
        """Imported ``pagar_only`` rows have no physical pickup to register."""
        Rental.objects.create(
            customer=self.customer,
            number=9003,
            pickup_date=self.today,
            return_date=self.today,
            status=Rental.Status.PENDING,
            legacy_notes=Rental.LEGACY_PAGAR_ONLY_MARKER,
        )

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(list(response.context['today_pickups']), [])

    def test_today_summary_has_an_empty_state_instead_of_a_blank_panel(self):
        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Nenhuma retirada marcada para hoje')
        self.assertContains(response, 'Nenhuma devolução marcada para hoje')

    def test_today_summary_is_hidden_without_the_movements_module(self):
        other = User.objects.create_user(
            email='no-movements@noivasecia.test', password='Senha12345',
        )
        grant(other, modules=['catalog'])
        self.client.force_login(other)

        response = self.client.get(reverse('dashboard'))

        self.assertNotContains(response, 'Resumo de hoje')

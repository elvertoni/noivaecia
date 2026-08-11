import re
from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from billing.models import CashAccount, Payment
from catalog.models import Category, Product
from customers.models import Customer
from rentals.forms import RentalForm
from rentals.models import Rental


User = get_user_model()


class ElementByIdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get('id')
        if element_id:
            self.elements[element_id] = {
                'tag': tag,
                'attrs': attributes,
            }


class RentalFooterUITests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='footer-ui@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=user,
            module_key='rentals',
            allowed=True,
        )
        self.client.force_login(user)
        self.response = self.client.get(reverse('rentals:create'))
        self.assertEqual(self.response.status_code, 200)
        self.html = self.response.content.decode()
        parser = ElementByIdParser()
        parser.feed(self.html)
        self.elements = parser.elements

    def classes_for(self, element_id):
        return set(
            self.elements[element_id]['attrs'].get('class', '').split()
        )

    def test_action_bar_uses_sidebar_offset_and_semantic_layering(self):
        action_classes = self.classes_for('rental-action-bar')
        self.assertTrue({
            'fixed',
            'bottom-0',
            'left-0',
            'right-0',
            'lg:left-64',
            'z-20',
        }.issubset(action_classes))
        self.assertNotIn('z-40', action_classes)

        self.assertIn('z-30', self.classes_for('sidebar-backdrop'))
        self.assertIn('z-40', self.classes_for('sidebar'))

    def test_action_bar_handles_safe_areas_and_narrow_viewports(self):
        action_classes = self.classes_for('rental-action-bar')
        self.assertTrue({
            'pb-[max(0.75rem,env(safe-area-inset-bottom))]',
            'pl-[max(1rem,env(safe-area-inset-left))]',
            'pr-[max(1rem,env(safe-area-inset-right))]',
        }.issubset(action_classes))

        button_group_classes = self.classes_for('rental-action-buttons')
        self.assertTrue({
            'grid',
            'w-full',
            'min-w-0',
            'grid-cols-1',
            'sm:flex',
            'sm:w-auto',
        }.issubset(button_group_classes))
        self.assertTrue({
            'h-28',
            'sm:h-20',
        }.issubset(self.classes_for('rental-action-bar-spacer')))

    def test_actions_keep_accessible_form_relationships(self):
        action_bar = self.elements['rental-action-bar']['attrs']
        self.assertEqual(action_bar.get('role'), 'region')
        self.assertEqual(
            action_bar.get('aria-label'),
            'Ações do formulário',
        )
        self.assertContains(self.response, '>Cancelar</a>', html=False)
        self.assertContains(self.response, 'form="rental-form"')
        self.assertContains(self.response, 'Salvar locação')
        self.assertContains(self.response, 'Salvar e Imprimir')
        self.assertContains(self.response, 'id="add-item-footer"')

    def test_only_the_footer_add_item_button_is_rendered(self):
        items_position = self.html.index('id="itens"')
        action_bar_position = self.html.index('id="rental-action-bar"')
        footer_add_position = self.html.index('id="add-item-footer"')

        self.assertLess(items_position, action_bar_position)
        self.assertLess(action_bar_position, footer_add_position)
        self.assertEqual(
            self.elements['add-item-footer']['attrs'].get('aria-controls'),
            'item-forms',
        )
        self.assertNotIn('add-item', self.elements)
        self.assertNotIn('add-item-bottom', self.elements)

    def test_totals_script_does_not_include_legacy_replacement_value(self):
        self.assertNotContains(
            self.response,
            'subtotal > 0 ? `R$ ${formatMoneyBR(subtotal)}`',
        )
        self.assertNotContains(self.response, 'Valor de reposição')
        self.assertNotContains(self.response, 'Multa diária por atraso')
        self.assertNotContains(self.response, 'Multa por atraso')


class RentalFormSubmittabilityTests(TestCase):
    """Guards against controls the browser refuses to validate (RF-15).

    A `required` attribute on a `display:none` control makes Chrome abort the
    submit with no visible message — the Save button simply appears dead. The
    customer select is hidden behind a JS combobox, so it must never carry it.
    """

    def setUp(self):
        user = User.objects.create_user(
            email='submittability@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=user, module_key='rentals', allowed=True,
        )
        self.client.force_login(user)
        self.html = self.client.get(reverse('rentals:create')).content.decode()

    def test_no_control_is_both_hidden_and_html_required(self):
        offenders = [
            tag for tag in re.findall(r'<(?:select|input|textarea)[^>]*>', self.html)
            if 'class="hidden"' in tag or 'class="hidden ' in tag
            if ' required' in tag
        ]

        self.assertEqual(
            offenders, [],
            'Um controle escondido com `required` trava o submit sem mensagem: '
            f'{offenders}',
        )

    def test_customer_select_keeps_server_side_validation(self):
        form = RentalForm()

        self.assertTrue(form.fields['customer'].required)


class RentalItemsGridScrollTests(TestCase):
    """The items grid must not be a scroll container (RF-16).

    `overflow-x` also promotes `overflow-y` to auto, which clipped the product
    dropdown inside the grid and stacked a third scrollbar on the page.
    """

    def setUp(self):
        user = User.objects.create_user(
            email='grid-scroll@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=user, module_key='rentals', allowed=True,
        )
        self.client.force_login(user)
        self.html = self.client.get(reverse('rentals:create')).content.decode()

    def test_items_grid_wrapper_has_no_overflow_container(self):
        wrapper = re.search(
            r'<div class="[^"]*"[^>]*>\s*<table class="data-table', self.html,
        )
        self.assertIsNotNone(wrapper, 'wrapper do grid de itens não encontrado')
        classes = wrapper.group(0)

        self.assertNotIn('overflow', classes)
        self.assertNotIn('table-shell', classes)

    def test_items_grid_no_longer_offers_a_wearer_column(self):
        self.assertNotIn('Quem vai usar</th>', self.html)
        self.assertNotIn('items-0-wearer_name', self.html)


class RentalItemRowPersistenceTests(TestCase):
    """A failed save must not silently discard rows the user typed into (RF-16)."""

    def setUp(self):
        user = User.objects.create_user(
            email='row-persist@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=user, module_key='rentals', allowed=True,
        )
        self.client.force_login(user)
        self.customer = Customer.objects.create(name='Cliente Persistência')
        category = Category.objects.create(prefix='SAP', name='Sapatos')
        self.product = Product.objects.create(
            category=category, code=1, description='SAPATO',
            color='PRETO', size='40', value=Decimal('120.00'),
        )
        CashAccount.objects.create(name='Caixa persistência')

    def _post(self, **overrides):
        data = {
            'customer': self.customer.pk,
            'pickup_date': '10/08/2026',
            'return_date': '15/08/2026',
            'penalty_value': '100,00',
            'wearer_name': '',
            'notes': '',
            'installment_count': '1',
            'down_payment_amount': '120,00',
            'down_payment_method': Payment.Method.CASH,
            'down_payment_date': '10/08/2026',
            'items-TOTAL_FORMS': '2',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': '',
            'items-0-product': self.product.pk,
            'items-0-value': '',          # forces the re-render
            'items-1-id': '',
            'items-1-product': '',
            'items-1-value': '',
        }
        data.update(overrides)
        return self.client.post(reverse('rentals:create'), data)

    def test_typed_product_text_survives_a_failed_save(self):
        response = self._post(**{'items-1-product_search': 'sapa'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="sapa"')

    def test_row_with_only_a_typed_price_is_not_discarded(self):
        response = self._post(**{'items-1-value': '80,00'})

        rendered = response.context['items'].visible_forms
        self.assertEqual(len(rendered), 2)

    def test_untouched_blank_slot_is_not_re_rendered(self):
        response = self._post()

        rendered = response.context['items'].visible_forms
        self.assertEqual(len(rendered), 1)

    def test_grid_never_renders_without_a_row(self):
        response = self._post(**{
            'items-0-product': '',
            'items-0-value': '',
        })

        rendered = response.context['items'].visible_forms
        self.assertGreaterEqual(len(rendered), 1)

    def test_typed_text_is_not_treated_as_a_form_field(self):
        """The echoed input must never leak into the saved item."""
        response = self._post(**{
            'items-0-value': '120,00',
            'items-0-product_search': 'texto qualquer',
        })

        self.assertEqual(response.status_code, 302)
        item = Rental.objects.get().items.get()
        self.assertEqual(item.product_id, self.product.pk)

import re
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from rentals.forms import RentalForm


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

    def test_totals_script_defines_and_updates_penalty(self):
        self.assertContains(
            self.response,
            "const penaltyEl = document.getElementById('penalty-display');",
        )
        self.assertContains(
            self.response,
            'penaltyEl.textContent = `R$ ${formatMoneyBR(penaltyVal)}`',
        )
        self.assertNotContains(
            self.response,
            'subtotal > 0 ? `R$ ${formatMoneyBR(subtotal)}`',
        )
        self.assertContains(self.response, 'Multa por atraso (valor único)')
        self.assertNotContains(self.response, 'Multa diária por atraso')


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

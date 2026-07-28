from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission


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
            'grid-cols-3',
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
        self.assertContains(self.response, 'id="add-item-footer"')

    def test_add_item_actions_remain_in_the_items_section(self):
        items_position = self.html.index('id="itens"')
        top_add_position = self.html.index('id="add-item"')
        bottom_add_position = self.html.index('id="add-item-bottom"')
        action_bar_position = self.html.index('id="rental-action-bar"')
        footer_add_position = self.html.index('id="add-item-footer"')

        self.assertLess(items_position, top_add_position)
        self.assertLess(top_add_position, bottom_add_position)
        self.assertLess(bottom_add_position, action_bar_position)
        self.assertLess(action_bar_position, footer_add_position)
        self.assertEqual(
            self.elements['add-item']['attrs'].get('aria-controls'),
            'item-forms',
        )
        self.assertEqual(
            self.elements['add-item-bottom']['attrs'].get('aria-controls'),
            'item-forms',
        )
        self.assertEqual(
            self.elements['add-item-footer']['attrs'].get('aria-controls'),
            'item-forms',
        )
        self.assertTrue({
            'w-full',
            'sm:w-auto',
        }.issubset(self.classes_for('add-item')))

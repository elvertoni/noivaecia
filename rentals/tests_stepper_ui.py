"""Guards for the stepped rental form (RF-17).

The stepper is progressive disclosure over a *single* ``<form>``: hiding a step
must never remove a control from the document, or the POST payload and the item
formset silently change shape. These tests lock that in, plus the sticky
summary bar and the keyboard-first shortcuts the counter relies on.
"""

import re
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission


User = get_user_model()


class FormStructureParser(HTMLParser):
    """Map every form control to the step panel that actually contains it.

    An earlier version collected control names from the whole response, so it
    stayed green even when the panels were detached from the form — it proved
    nothing. Nesting is tracked here so each control is attributed to its panel.
    """

    SUBMIT_CAPABLE = {'button', 'input'}

    def __init__(self):
        super().__init__()
        self.panels = []                 # [{'index', 'id', 'hidden'}]
        self.controls_by_panel = {}      # panel index -> {control names}
        self.controls_outside_panels = set()
        self.submitters = []             # (tag, name) in document order
        self._panel_stack = []
        self._depth = 0
        self._form_depth = None
        self._panel_depths = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._depth += 1

        if tag == 'form' and a.get('id') == 'rental-form':
            self._form_depth = self._depth

        if 'data-step-panel' in a:
            index = a['data-step-panel']
            self.panels.append({
                'index': index,
                'id': a.get('id'),
                'hidden': 'hidden' in a,
                'inside_form': self._form_depth is not None,
            })
            self._panel_stack.append(index)
            self._panel_depths[index] = self._depth
            self.controls_by_panel.setdefault(index, set())

        if tag in {'input', 'select', 'textarea'} and a.get('name'):
            if self._panel_stack:
                self.controls_by_panel[self._panel_stack[-1]].add(a['name'])
            else:
                self.controls_outside_panels.add(a['name'])

        # Implicit submission picks the first submit-capable control in tree
        # order, which is not necessarily a <button>.
        if tag in self.SUBMIT_CAPABLE and self._form_depth is not None:
            if (a.get('type') or '').lower() == 'submit':
                self.submitters.append((tag, a.get('name') or ''))

    def handle_endtag(self, tag):
        for index, depth in list(self._panel_depths.items()):
            if depth == self._depth and self._panel_stack and self._panel_stack[-1] == index:
                self._panel_stack.pop()
        if self._form_depth == self._depth:
            self._form_depth = None
        self._depth -= 1


class RentalStepperTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='stepper-ui@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(user=user, module_key='rentals', allowed=True)
        self.client.force_login(user)
        response = self.client.get(reverse('rentals:create'))
        self.assertEqual(response.status_code, 200)
        self.html = response.content.decode()
        self.parser = FormStructureParser()
        self.parser.feed(self.html)

    def test_three_steps_are_rendered_with_only_the_first_visible(self):
        panels = self.parser.panels

        self.assertEqual([panel['index'] for panel in panels], ['1', '2', '3'])
        self.assertEqual(
            [panel['hidden'] for panel in panels],
            [False, True, True],
            'A locação deve abrir na etapa 1 com as demais recolhidas.',
        )

    def test_every_step_is_nested_inside_the_single_rental_form(self):
        self.assertTrue(self.parser.panels)
        for panel in self.parser.panels:
            self.assertTrue(
                panel['inside_form'],
                f'Etapa {panel["index"]} saiu de dentro de #rental-form: '
                'o POST deixaria de carregar os campos dela.',
            )

    def test_each_step_owns_the_controls_the_payload_depends_on(self):
        """Attributed per panel: a detached panel makes this fail, not pass."""
        por_etapa = self.parser.controls_by_panel

        self.assertLessEqual({'customer', 'pickup_date', 'return_date'}, por_etapa.get('1', set()))
        self.assertLessEqual(
            {'items-TOTAL_FORMS', 'items-0-product', 'items-0-value'},
            por_etapa.get('2', set()),
        )
        self.assertLessEqual(
            {'down_payment_amount', 'installment_count', 'notes'},
            por_etapa.get('3', set()),
        )

    def test_steps_never_leave_the_dom_when_hidden(self):
        """The whole design rests on hiding panels, never detaching them."""
        script = self.html[self.html.index('id="rental-stepper"'):]

        self.assertIn('panel.hidden =', script)
        self.assertNotIn('panel.remove()', script)
        self.assertNotIn('panel.parentNode.removeChild', script)

    def test_steps_are_navigable_and_announced(self):
        self.assertIn('id="rental-stepper"', self.html)
        for index in ('1', '2', '3'):
            self.assertIn(f'data-step-target="{index}"', self.html)
        self.assertIn('id="step-announcer"', self.html)

    def test_sticky_bar_total_is_written_by_the_totals_routine(self):
        """The mirror must live inside the function that computes the total."""
        inicio = self.html.index('function updateFinancialTotals()')
        corpo = self.html[inicio:self.html.index('\n  }', inicio)]

        self.assertIn("getElementById('bar-total-display')", corpo)
        self.assertRegex(
            corpo,
            r'barTotalEl\.textContent\s*=',
            'A barra fixa parou de receber o total calculado.',
        )
        self.assertIn("getElementById('total-final-display')", corpo)

    def test_item_counter_is_written_by_the_renumbering_routine(self):
        inicio = self.html.index('function renumberItems()')
        corpo = self.html[inicio:self.html.index('\n  }', inicio)]

        self.assertRegex(corpo, r'countBadge\.textContent\s*=')
        self.assertRegex(corpo, r'barCount\.textContent\s*=')

    def test_keyboard_first_shortcuts_survive_the_stepper(self):
        self.assertIn("e.key === 'F2'", self.html)
        self.assertIn("e.key.toLowerCase() === 's'", self.html)
        self.assertIn('id="item-count-badge"', self.html)

    def test_enter_on_the_last_field_of_a_step_advances_instead_of_saving(self):
        """Otherwise Enter falls into implicit submission mid-form (RF-17).

        The counter fills this screen by keyboard; reaching the end of step 1
        and getting a form covered in validation errors is a dead end.
        """
        self.assertIn('goToStep(currentStep + 1', self.html)
        self.assertIn('controls[controls.length - 1] !== target', self.html)

    def test_implicit_submission_never_lands_on_save_and_print(self):
        """Enter must save, not open the print view.

        The visible buttons live in the fixed bar *outside* the form, and the
        first of them posts ``save_and_print=1``; the first submit-capable
        control inside the form has to win the implicit-submission lookup.
        """
        self.assertTrue(self.parser.submitters, 'form sem botão de submit próprio')
        primeiro_tag, primeiro_name = self.parser.submitters[0]

        self.assertNotEqual(
            primeiro_name,
            'save_and_print',
            f'O primeiro submit do form é <{primeiro_tag} name="{primeiro_name}">, '
            'que dispararia a impressão em vez de salvar.',
        )

    def test_step_revealer_is_registered_after_app_js_is_available(self):
        """`app.js` is deferred: registering inline would silently no-op.

        Without this the validation cannot reopen the step holding a rejected
        field, and the form just refuses to submit with nothing on screen.
        """
        self.assertIn('registerStepRevealer', self.html)
        self.assertRegex(
            self.html,
            r"document\.addEventListener\('DOMContentLoaded',\s*registerStepRevealer\)",
        )

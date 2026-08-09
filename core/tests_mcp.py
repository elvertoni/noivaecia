"""Focused tests for the optional administrative MCP integration."""

import ast
import base64
import inspect
import json
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import resolve


@skipUnless(settings.MCP_ENABLED, 'Optional MCP dependencies are not installed.')
class AdminMCPToolsetTests(TestCase):
    """Exercise authorization, discovery, CRUD and metrics directly."""

    @classmethod
    def setUpTestData(cls):
        user_model = settings.AUTH_USER_MODEL
        from django.apps import apps

        model = apps.get_model(user_model)
        cls.admin = model.objects.create_user(
            email='mcp-admin@example.com',
            password='Mcp-test-password-2026',
            is_staff=True,
        )
        cls.regular_user = model.objects.create_user(
            email='mcp-user@example.com',
            password='Mcp-test-password-2026',
        )

    def toolset(self, user=None):
        from core.mcp import AdminMCPToolset

        return AdminMCPToolset(
            request=SimpleNamespace(user=user or self.admin)
        )

    def test_every_public_tool_starts_with_admin_guard(self):
        from core.mcp import AdminMCPToolset

        tree = ast.parse(inspect.getsource(AdminMCPToolset))
        class_node = tree.body[0]
        public_methods = [
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith('_')
        ]
        self.assertTrue(public_methods)
        for method in public_methods:
            body = method.body[1:] if ast.get_docstring(method) else method.body
            first = body[0]
            self.assertIsInstance(first, ast.Expr, method.name)
            self.assertIsInstance(first.value, ast.Call, method.name)
            self.assertIsInstance(first.value.func, ast.Attribute, method.name)
            self.assertEqual(first.value.func.attr, '_require_admin', method.name)

    def test_non_admin_cannot_call_tools(self):
        with self.assertRaises(PermissionDenied):
            self.toolset(self.regular_user).list_entities()

    def test_catalog_covers_every_concrete_local_model(self):
        from django.apps import apps

        from core.mcp import ENTITIES

        local_apps = {
            'accounts',
            'billing',
            'catalog',
            'company',
            'core',
            'customers',
            'movements',
            'notifications',
            'rentals',
        }
        expected = {
            model._meta.label
            for model in apps.get_models()
            if model._meta.app_label in local_apps and not model._meta.abstract
        }
        self.assertEqual(set(ENTITIES.values()), expected)

    def test_category_crud_and_audit(self):
        from catalog.models import Category
        from core.models import AuditLog

        toolset = self.toolset()
        created = toolset.create_record(
            'category',
            {'prefix': 'MCP', 'name': 'Categoria MCP'},
        )
        category_id = created['id']
        self.assertEqual(created['prefix'], 'MCP')
        self.assertEqual(
            toolset.get_record('category', category_id)['name'],
            'Categoria MCP',
        )

        updated = toolset.update_record(
            'category',
            category_id,
            {'name': 'Categoria MCP Atualizada'},
        )
        self.assertEqual(updated['name'], 'Categoria MCP Atualizada')
        listed = toolset.list_records('category', search='Atualizada')
        self.assertEqual(listed['total'], 1)
        self.assertEqual(
            toolset.count_records('category', filters={'prefix': 'MCP'})[
                'total'
            ],
            1,
        )

        deleted = toolset.delete_record('category', category_id)
        self.assertEqual(deleted['id'], category_id)
        self.assertFalse(Category.objects.filter(pk=category_id).exists())
        self.assertEqual(
            AuditLog.objects.filter(action__startswith='mcp_').count(),
            3,
        )

    def test_user_password_is_hashed_and_never_serialized(self):
        user_model = self.admin.__class__
        toolset = self.toolset()
        created = toolset.create_record(
            'user',
            {
                'email': 'created-by-mcp@example.com',
                'password': 'Another-test-password-2026',
                'is_active': True,
                'is_staff': False,
                'is_superuser': False,
            },
        )
        self.assertNotIn('password', created)
        user = user_model.objects.get(pk=created['id'])
        self.assertTrue(user.check_password('Another-test-password-2026'))
        password_specs = [
            field
            for field in toolset.describe_entity('user')['fields']
            if field['name'] == 'password'
        ]
        self.assertEqual(len(password_specs), 1)
        self.assertTrue(password_specs[0]['write_only'])

    def test_authenticated_user_cannot_delete_itself(self):
        with self.assertRaisesMessage(ValueError, 'próprio usuário'):
            self.toolset().delete_record('user', self.admin.pk)

    def test_rental_with_operational_history_cannot_be_deleted(self):
        from datetime import date

        from customers.models import Customer
        from movements.models import Pickup
        from rentals.models import Rental

        customer = Customer.objects.create(name='CLIENTE MCP')
        rental = Rental.objects.create(
            number=987654,
            customer=customer,
            pickup_date=date(2026, 8, 1),
            return_date=date(2026, 8, 2),
            status=Rental.Status.CANCELLED,
        )
        Pickup.objects.create(rental=rental, pickup_date=date(2026, 8, 1))

        with self.assertRaisesMessage(ValueError, 'preservada para auditoria'):
            self.toolset().delete_record('rental', rental.pk)
        self.assertTrue(Rental.objects.filter(pk=rental.pk).exists())

    def test_metrics_return_serializable_dictionaries(self):
        toolset = self.toolset()
        for result in (
            toolset.general_metrics(),
            toolset.rental_metrics(),
            toolset.billing_metrics(),
            toolset.catalog_metrics(),
            toolset.notification_metrics(),
            toolset.system_usage(),
        ):
            self.assertIsInstance(result, dict)

    def test_project_is_not_tenant_aware(self):
        from catalog.models import Category
        from core.mcp import TENANT_FK

        self.assertIsNone(TENANT_FK)
        self.assertFalse(self.toolset()._is_tenant_aware(Category))


@skipUnless(settings.MCP_ENABLED, 'Optional MCP dependencies are not installed.')
class MCPHttpConfigurationTests(TestCase):
    """Verify endpoint and Basic authentication configuration."""

    @classmethod
    def setUpTestData(cls):
        from django.apps import apps

        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        cls.admin = user_model.objects.create_user(
            email='mcp-http-admin@example.com',
            password='Mcp-http-password-2026',
            is_staff=True,
        )

    def basic_header(self):
        credentials = base64.b64encode(
            b'mcp-http-admin@example.com:Mcp-http-password-2026'
        ).decode()
        return f'Basic {credentials}'

    def test_mcp_route_is_exact_and_requires_authentication(self):
        match = resolve('/mcp')
        self.assertEqual(match.url_name, 'mcp_server_streamable_http_endpoint')
        response = self.client.post(
            '/mcp',
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response['WWW-Authenticate'].startswith('Basic'))

    def test_admin_can_initialize_streamable_http_session(self):
        response = self.client.post(
            '/mcp',
            data=json.dumps({
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2025-03-26',
                    'capabilities': {},
                    'clientInfo': {'name': 'django-test', 'version': '1.0'},
                },
            }),
            content_type='application/json',
            HTTP_ACCEPT='application/json, text/event-stream',
            HTTP_AUTHORIZATION=self.basic_header(),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['jsonrpc'], '2.0')
        self.assertEqual(payload['id'], 1)
        self.assertIn('serverInfo', payload['result'])

    def test_basic_authentication_is_the_only_configured_class(self):
        self.assertEqual(
            settings.DJANGO_MCP_AUTHENTICATION_CLASSES,
            ['rest_framework.authentication.BasicAuthentication'],
        )

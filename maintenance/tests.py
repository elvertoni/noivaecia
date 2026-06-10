from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ModulePermission

User = get_user_model()


class MaintenanceAccessTests(TestCase):
    def test_maintenance_requires_login(self):
        response = self.client.get('/manutencao/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_maintenance_requires_module_permission(self):
        user = User.objects.create_user(email='plain@b.com', password='Senha12345')
        self.client.force_login(user)

        self.assertEqual(self.client.get('/manutencao/').status_code, 403)

    def test_maintenance_permission_allows_access(self):
        user = User.objects.create_user(email='ops@b.com', password='Senha12345')
        ModulePermission.objects.create(
            user=user,
            module_key='maintenance',
            allowed=True,
        )
        self.client.force_login(user)

        response = self.client.get('/manutencao/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resumo do banco')

    def test_maintenance_actions_require_module_permission(self):
        user = User.objects.create_user(email='plain@b.com', password='Senha12345')
        self.client.force_login(user)

        self.assertEqual(
            self.client.post('/manutencao/recalcular-totais/').status_code,
            403,
        )

        ModulePermission.objects.create(
            user=user,
            module_key='maintenance',
            allowed=True,
        )

        response = self.client.post('/manutencao/recalcular-totais/')

        self.assertEqual(response.status_code, 302)

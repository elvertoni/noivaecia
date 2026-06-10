from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ModulePermission

User = get_user_model()


class DashboardModuleTests(TestCase):
    def test_dashboard_only_lists_allowed_modules(self):
        user = User.objects.create_user(email='ops@b.com', password='Senha12345')
        ModulePermission.objects.create(
            user=user,
            module_key='customers',
            allowed=True,
        )
        self.client.force_login(user)

        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Clientes')
        self.assertNotContains(response, 'Manutenção')

    def test_dashboard_lists_maintenance_when_allowed(self):
        user = User.objects.create_user(email='maint@b.com', password='Senha12345')
        ModulePermission.objects.create(
            user=user,
            module_key='maintenance',
            allowed=True,
        )
        self.client.force_login(user)

        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Manutenção')

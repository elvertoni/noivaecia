import re

from django.core import mail
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import ModulePermission

User = get_user_model()


class UserModelTests(TestCase):
    def test_create_user_with_email(self):
        user = User.objects.create_user(email='a@b.com', password='Senha12345')
        self.assertEqual(user.email, 'a@b.com')
        self.assertTrue(user.check_password('Senha12345'))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_user_without_email_raises(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email='', password='x')

    def test_create_superuser(self):
        admin = User.objects.create_superuser(email='admin@b.com', password='Senha12345')
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_username_field_is_email(self):
        self.assertEqual(User.USERNAME_FIELD, 'email')

    def test_timestamps_present(self):
        user = User.objects.create_user(email='t@b.com', password='Senha12345')
        self.assertIsNotNone(user.created_at)
        self.assertIsNotNone(user.updated_at)

    def test_has_module(self):
        user = User.objects.create_user(email='u@b.com', password='Senha12345')
        self.assertFalse(user.has_module('customers'))
        ModulePermission.objects.create(user=user, module_key='customers', allowed=True)
        self.assertTrue(user.has_module('customers'))

    def test_superuser_has_all_modules(self):
        admin = User.objects.create_superuser(email='s@b.com', password='Senha12345')
        self.assertTrue(admin.has_module('anything'))


class AuthFlowTests(TestCase):
    def test_signup_creates_user_and_redirects_to_login(self):
        response = self.client.post('/signup/', {
            'email': 'novo@b.com',
            'first_name': 'Ana',
            'password1': 'Abcd!2345x',
            'password2': 'Abcd!2345x',
        })
        self.assertRedirects(response, '/login/')
        self.assertTrue(User.objects.filter(email='novo@b.com').exists())

    def test_login_by_email(self):
        User.objects.create_user(email='log@b.com', password='Senha12345')
        ok = self.client.login(email='log@b.com', password='Senha12345')
        self.assertTrue(ok)

    def test_login_page_links_to_password_reset(self):
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Esqueci minha senha')
        self.assertContains(response, '/senha/redefinir/')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='Noivas & Cia <no-reply@example.com>',
    )
    def test_password_reset_email_allows_password_change(self):
        user = User.objects.create_user(email='reset@b.com', password='Senha12345')

        response = self.client.post('/senha/redefinir/', {'email': 'reset@b.com'})

        self.assertRedirects(response, '/senha/redefinir/enviada/')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Redefinição de senha', mail.outbox[0].subject)

        match = re.search(
            r'http://testserver(?P<path>/senha/redefinir/confirmar/\S+)',
            mail.outbox[0].body,
        )
        self.assertIsNotNone(match)

        response = self.client.get(match.group('path'))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(response.request['PATH_INFO'], {
            'new_password1': 'NovaSenha!2345',
            'new_password2': 'NovaSenha!2345',
        })

        self.assertRedirects(response, '/senha/redefinir/concluida/')
        user.refresh_from_db()
        self.assertTrue(user.check_password('NovaSenha!2345'))

    def test_dashboard_requires_login(self):
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_user_list_requires_staff(self):
        User.objects.create_user(email='plain@b.com', password='Senha12345')
        self.client.login(email='plain@b.com', password='Senha12345')
        self.assertEqual(self.client.get('/users/').status_code, 403)
        admin = User.objects.create_superuser(email='adm@b.com', password='Senha12345')
        self.client.force_login(admin)
        self.assertEqual(self.client.get('/users/').status_code, 200)

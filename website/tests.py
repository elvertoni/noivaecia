from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class HomeViewTests(TestCase):
    def test_public_home_renders_login_entry_point(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'website/home.html')
        self.assertContains(response, 'Entrar no sistema')
        self.assertContains(response, reverse('login'))
        self.assertNotContains(response, 'Ir para o painel')

    def test_authenticated_home_links_directly_to_dashboard(self):
        user = User.objects.create_user(
            email='website@test.com',
            password='safe-password',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Ir para o painel')
        self.assertContains(response, reverse('dashboard'))
        self.assertNotContains(response, 'Entrar no sistema')

    def test_home_has_single_main_heading_and_semantic_feature_list(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, '<h1', count=1)
        self.assertContains(response, 'aria-label="Recursos do sistema"')
        self.assertContains(response, '<h2', count=3)

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts import login_throttle
from accounts.models import LoginAttempt

User = get_user_model()


class LoginThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='throttle@noivasecia.test',
            password='Senha12345',
        )
        self.login_url = reverse('login')

    def _fail(self, times):
        for _ in range(times):
            self.client.post(self.login_url, {
                'username': 'throttle@noivasecia.test',
                'password': 'senha-errada',
            })

    def test_allows_a_few_failed_attempts(self):
        self._fail(login_throttle.MAX_FAILED_ATTEMPTS - 1)
        response = self.client.post(self.login_url, {
            'username': 'throttle@noivasecia.test',
            'password': 'Senha12345',
        })
        self.assertEqual(response.status_code, 302)

    def test_locks_out_after_max_failed_attempts(self):
        self._fail(login_throttle.MAX_FAILED_ATTEMPTS)
        response = self.client.post(self.login_url, {
            'username': 'throttle@noivasecia.test',
            'password': 'Senha12345',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, login_throttle.LOCKOUT_MESSAGE)

    def test_lockout_is_scoped_to_the_email_not_global(self):
        self._fail(login_throttle.MAX_FAILED_ATTEMPTS)
        other = User.objects.create_user(
            email='other@noivasecia.test',
            password='Senha12345',
        )
        response = self.client.post(self.login_url, {
            'username': 'other@noivasecia.test',
            'password': 'Senha12345',
        })
        self.assertEqual(response.status_code, 302)

    def test_successful_login_clears_prior_failed_attempts(self):
        self._fail(login_throttle.MAX_FAILED_ATTEMPTS - 1)
        self.client.post(self.login_url, {
            'username': 'throttle@noivasecia.test',
            'password': 'Senha12345',
        })
        self.assertFalse(
            LoginAttempt.objects.filter(email='throttle@noivasecia.test').exists()
        )

    def test_is_locked_out_ignores_attempts_outside_the_window(self):
        from django.utils import timezone
        attempt = LoginAttempt.objects.create(email='old@noivasecia.test')
        LoginAttempt.objects.filter(pk=attempt.pk).update(
            created_at=timezone.now() - timezone.timedelta(
                minutes=login_throttle.LOCKOUT_WINDOW_MINUTES + 1,
            )
        )
        for _ in range(login_throttle.MAX_FAILED_ATTEMPTS - 1):
            LoginAttempt.objects.create(email='old@noivasecia.test')

        self.assertFalse(login_throttle.is_locked_out('old@noivasecia.test'))

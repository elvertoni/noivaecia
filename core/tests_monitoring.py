from django.test import RequestFactory, SimpleTestCase

from noivas_cia.middleware import MetricsHostMiddleware


class MetricsHostMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_metrics_rewrites_internal_host_only(self):
        request = self.factory.get('/metrics', HTTP_HOST='10.0.1.18:8000')
        middleware = MetricsHostMiddleware(lambda current: current)

        response = middleware(request)

        self.assertEqual(response.META['HTTP_HOST'], 'localhost')

    def test_other_paths_preserve_original_host(self):
        request = self.factory.get('/healthz/', HTTP_HOST='10.0.1.18:8000')
        middleware = MetricsHostMiddleware(lambda current: current)

        response = middleware(request)

        self.assertEqual(response.META['HTTP_HOST'], '10.0.1.18:8000')

    def test_metrics_forwarded_by_traefik_is_not_exposed(self):
        request = self.factory.get(
            '/metrics',
            HTTP_HOST='noivaseciabandeirantes.com.br',
            HTTP_X_FORWARDED_PROTO='https',
        )
        middleware = MetricsHostMiddleware(lambda current: current)

        response = middleware(request)

        self.assertEqual(response.status_code, 404)

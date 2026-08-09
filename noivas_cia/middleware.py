"""Project-specific middleware."""

from django.http import HttpResponseNotFound


class MetricsHostMiddleware:
    """Allow internal ``/metrics`` scrapes without loosening public hosts.

    Prometheus connects to the container IP, so the request Host is a dynamic
    address outside ``ALLOWED_HOSTS``. Requests forwarded by Traefik are denied;
    rewriting only direct internal scrapes keeps host validation unchanged
    everywhere else.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/metrics':
            # The application's Host router otherwise also matches /metrics.
            # Traefik always adds X-Forwarded-Proto, while Prometheus connects
            # directly to the task without proxy headers. Fail closed publicly.
            if request.META.get('HTTP_X_FORWARDED_PROTO'):
                return HttpResponseNotFound()
            request.META['HTTP_HOST'] = 'localhost'
        return self.get_response(request)

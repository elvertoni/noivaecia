"""
URL configuration for noivas_cia project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('website.urls')),
    path('', include('core.urls')),
    path('', include('accounts.urls')),
    path('clientes/', include('customers.urls')),
    path('catalogo/', include('catalog.urls')),
    path('empresa/', include('company.urls')),
    path('locacoes/', include('rentals.urls')),
    path('movimentacao/', include('movements.urls')),
    path('avisos-whatsapp/', include('notifications.urls')),
    path('financeiro/', include('billing.urls')),
    path('relatorios/', include('reports.urls')),
    path('manutencao/', include('maintenance.urls')),
]

# Scraped only through the internal Swarm network. Middleware rejects requests
# forwarded by Traefik, and the route exists only with instrumentation enabled.
if getattr(settings, 'PROMETHEUS_ENABLED', False):
    urlpatterns += [path('', include('django_prometheus.urls'))]

# mcp_server.urls owns the exact /mcp path. It is imported only when both
# optional dependencies are available, preserving the current boot otherwise.
if getattr(settings, 'MCP_ENABLED', False):
    urlpatterns += [path('', include('mcp_server.urls'))]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

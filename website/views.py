from django.views.generic import TemplateView


class HomeView(TemplateView):
    """Public presentation page with a login CTA (RF-01..RF-03)."""

    template_name = 'website/home.html'

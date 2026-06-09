from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView
from django.views.generic.detail import SingleObjectMixin

from core.modules import MODULES

from .forms import EmailUserCreationForm
from .models import ModulePermission, User


class SignupView(CreateView):
    """Public email signup (RF-06). Redirects to login on success."""

    form_class = EmailUserCreationForm
    template_name = 'accounts/signup.html'
    success_url = reverse_lazy('login')

    def form_valid(self, form):
        messages.success(self.request, 'Conta criada com sucesso. Faça login.')
        return super().form_valid(form)


class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict a view to staff/admin users."""

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and user.is_staff


class UserListView(StaffRequiredMixin, ListView):
    """Admin listing of users with their granted module count (RF-08)."""

    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    ordering = ('email',)

    def get_queryset(self):
        return super().get_queryset().annotate(
            allowed_modules=Count(
                'module_permissions',
                filter=Q(module_permissions__allowed=True),
            )
        )


class UserPermissionsView(StaffRequiredMixin, SingleObjectMixin, ListView):
    """Manage per-module access for a single user (RF-08, RF-09)."""

    template_name = 'accounts/user_permissions.html'

    def get(self, request, *args, **kwargs):
        self.object = get_object_or_404(User, pk=kwargs['pk'])
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return User.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed = set(
            self.object.module_permissions.filter(allowed=True)
            .values_list('module_key', flat=True)
        )
        context['target_user'] = self.object
        context['modules'] = [
            {'key': key, 'label': label, 'allowed': key in allowed}
            for key, label in MODULES
        ]
        return context

    def post(self, request, *args, **kwargs):
        user = get_object_or_404(User, pk=kwargs['pk'])
        selected = set(request.POST.getlist('modules'))
        for key, _ in MODULES:
            ModulePermission.objects.update_or_create(
                user=user,
                module_key=key,
                defaults={'allowed': key in selected},
            )
        messages.success(request, 'Permissões atualizadas.')
        return redirect('user_permissions', pk=user.pk)

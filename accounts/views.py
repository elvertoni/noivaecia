from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core import signing
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView
from django.views.generic.detail import SingleObjectMixin

from core.modules import MODULES

from .forms import EmailUserCreationForm
from .models import ActionPermission, ModulePermission, User

REVOCATION_SALT = 'accounts.permission-revocation'


def _revocation_token(user, selected, revoked, permission_type):
    return signing.dumps(
        {
            'user_id': user.pk,
            'selected': sorted(selected),
            'revoked': sorted(revoked),
            'type': permission_type,
        },
        salt=REVOCATION_SALT,
        compress=True,
    )


def _revocation_is_confirmed(request, user, selected, revoked, permission_type):
    token = request.POST.get('revocation_token', '')
    if request.POST.get('confirm_revocation') != 'yes' or not token:
        return False
    try:
        payload = signing.loads(token, salt=REVOCATION_SALT, max_age=900)
    except signing.BadSignature:
        return False
    return payload == {
        'user_id': user.pk,
        'selected': sorted(selected),
        'revoked': sorted(revoked),
        'type': permission_type,
    }


class UserManagementRequiredMixin(UserPassesTestMixin):
    """Restrict user administration to the configured account owner."""

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and user.can_manage_users()


class SignupView(UserManagementRequiredMixin, CreateView):
    """Create users from the internal administration flow."""

    form_class = EmailUserCreationForm
    template_name = 'accounts/signup.html'
    success_url = reverse_lazy('user_list')

    def form_valid(self, form):
        with transaction.atomic():
            self.object = form.save()
            form.save_module_permissions(self.object)
        messages.success(self.request, 'Usuário criado com sucesso.')
        return redirect(self.get_success_url())


class UserListView(UserManagementRequiredMixin, ListView):
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


class UserPermissionsView(UserManagementRequiredMixin, SingleObjectMixin, ListView):
    """Manage per-module access for a single user (RF-08, RF-09)."""

    template_name = 'accounts/user_permissions.html'

    def get(self, request, *args, **kwargs):
        self.object = get_object_or_404(User, pk=kwargs['pk'])
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return User.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed = getattr(self, 'pending_selected', None)
        if allowed is None:
            allowed = set(
                self.object.module_permissions.filter(allowed=True)
                .values_list('module_key', flat=True)
            )
        context['target_user'] = self.object
        context['modules'] = [
            {'key': key, 'label': label, 'allowed': key in allowed}
            for key, label in MODULES
        ]
        context['revoked_permissions'] = getattr(self, 'revoked_permissions', [])
        context['revocation_token'] = getattr(self, 'revocation_token', '')
        return context

    def post(self, request, *args, **kwargs):
        user = get_object_or_404(User, pk=kwargs['pk'])
        selected = set(request.POST.getlist('modules'))
        current = set(
            user.module_permissions.filter(allowed=True)
            .values_list('module_key', flat=True)
        )
        revoked = current - selected
        if revoked and not _revocation_is_confirmed(
            request, user, selected, revoked, 'modules'
        ):
            labels = dict(MODULES)
            self.object = user
            self.object_list = self.get_queryset()
            self.pending_selected = selected
            self.revoked_permissions = [labels[key] for key in sorted(revoked)]
            self.revocation_token = _revocation_token(
                user, selected, revoked, 'modules'
            )
            return self.render_to_response(self.get_context_data())
        with transaction.atomic():
            for key, _ in MODULES:
                ModulePermission.objects.update_or_create(
                    user=user,
                    module_key=key,
                    defaults={'allowed': key in selected},
                )
        messages.success(request, 'Permissões atualizadas.')
        return redirect('user_permissions', pk=user.pk)


class UserActionPermissionsView(UserManagementRequiredMixin, SingleObjectMixin, ListView):
    """Manage fine-grained action permissions for a single user (R12.01)."""

    template_name = 'accounts/user_action_permissions.html'

    def get(self, request, *args, **kwargs):
        self.object = get_object_or_404(User, pk=kwargs['pk'])
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return User.objects.none()

    def get_context_data(self, **kwargs):
        from core.actions import ACTIONS
        context = super().get_context_data(**kwargs)
        allowed = getattr(self, 'pending_selected', None)
        if allowed is None:
            allowed = set(
                self.object.action_permissions.filter(allowed=True)
                .values_list('action_key', flat=True)
            )
        context['target_user'] = self.object
        context['actions'] = [
            {'key': key, 'label': label, 'allowed': key in allowed}
            for key, label in ACTIONS
        ]
        context['revoked_permissions'] = getattr(self, 'revoked_permissions', [])
        context['revocation_token'] = getattr(self, 'revocation_token', '')
        return context

    def post(self, request, *args, **kwargs):
        from core.actions import ACTION_KEYS, ACTION_LABELS
        user = get_object_or_404(User, pk=kwargs['pk'])
        selected = set(request.POST.getlist('actions'))
        current = set(
            user.action_permissions.filter(allowed=True)
            .values_list('action_key', flat=True)
        )
        revoked = current - selected
        if revoked and not _revocation_is_confirmed(
            request, user, selected, revoked, 'actions'
        ):
            self.object = user
            self.object_list = self.get_queryset()
            self.pending_selected = selected
            self.revoked_permissions = [ACTION_LABELS[key] for key in sorted(revoked)]
            self.revocation_token = _revocation_token(
                user, selected, revoked, 'actions'
            )
            return self.render_to_response(self.get_context_data())
        with transaction.atomic():
            for key in ACTION_KEYS:
                ActionPermission.objects.update_or_create(
                    user=user,
                    action_key=key,
                    defaults={'allowed': key in selected},
                )
        messages.success(request, 'Permissões de ação atualizadas.')
        return redirect('user_action_permissions', pk=user.pk)

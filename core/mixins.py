from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied


class StaffRequiredMixin(UserPassesTestMixin):
    """Restrict a view to authenticated staff/admin users (RF-25)."""

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and user.is_staff


class ModuleAccessMixin(LoginRequiredMixin):
    """Gate a module view behind login and per-module permission.

    Set ``module_key`` on the view to identify the protected module. Access is
    granted to superusers and to users with an allowed ModulePermission for that
    key (see ``accounts.User.has_module``). Unauthenticated users are redirected
    to login by LoginRequiredMixin; authenticated-but-unauthorized users get a
    403.
    """

    module_key = None

    def has_module_permission(self):
        if self.module_key is None:
            return True
        return self.request.user.has_module(self.module_key)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not self.has_module_permission():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ActionRequiredMixin:
    """Gate POST/PUT/PATCH/DELETE mutations behind a fine-grained action permission.

    Set ``action_key`` (e.g. 'customers.delete') on the view. GET requests pass
    through so the page renders normally; templates hide buttons via ``has_action``.
    Unauthenticated users are handled upstream by ModuleAccessMixin. Authenticated
    users without the permission get 403 on mutating requests.
    Superusers always pass.
    """

    action_key = None

    def dispatch(self, request, *args, **kwargs):
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and self.action_key:
            if not request.user.is_authenticated or not request.user.has_action(self.action_key):
                raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

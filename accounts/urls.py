from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.urls import path

from .forms import (
    EmailAuthenticationForm,
    EmailPasswordResetForm,
    StyledSetPasswordForm,
)
from .views import SignupView, UserListView, UserPermissionsView

urlpatterns = [
    path('users/new/', SignupView.as_view(), name='user_create'),
    path('signup/', SignupView.as_view(), name='signup'),
    path(
        'login/',
        LoginView.as_view(
            template_name='accounts/login.html',
            authentication_form=EmailAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name='login',
    ),
    path('logout/', LogoutView.as_view(), name='logout'),
    path(
        'senha/redefinir/',
        PasswordResetView.as_view(
            template_name='accounts/password_reset_form.html',
            email_template_name='accounts/password_reset_email.html',
            subject_template_name='accounts/password_reset_subject.txt',
            form_class=EmailPasswordResetForm,
        ),
        name='password_reset',
    ),
    path(
        'senha/redefinir/enviada/',
        PasswordResetDoneView.as_view(
            template_name='accounts/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'senha/redefinir/confirmar/<uidb64>/<token>/',
        PasswordResetConfirmView.as_view(
            template_name='accounts/password_reset_confirm.html',
            form_class=StyledSetPasswordForm,
        ),
        name='password_reset_confirm',
    ),
    path(
        'senha/redefinir/concluida/',
        PasswordResetCompleteView.as_view(
            template_name='accounts/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
    path('users/', UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/permissions/', UserPermissionsView.as_view(), name='user_permissions'),
]

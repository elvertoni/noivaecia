from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from .forms import EmailAuthenticationForm
from .views import SignupView, UserListView, UserPermissionsView

urlpatterns = [
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
    path('users/', UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/permissions/', UserPermissionsView.as_view(), name='user_permissions'),
]

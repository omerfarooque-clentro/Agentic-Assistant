from rest_framework_simplejwt.views import TokenRefreshView
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from core.views import RegistrationView, LoginView
from frontend.views import dashboard_view, login_view, registration_view, settings_view

urlpatterns = [
    path('', dashboard_view, name='dashboard'),
    path('signin/', login_view, name='frontend-login'),
    path('register/', registration_view, name='frontend-register'),
    path('settings/', settings_view, name='frontend-settings'),
    path('admin/', admin.site.urls),
    path('registration/', RegistrationView.as_view()),
    path('login/', LoginView.as_view()),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('api/', include('agent.urls')),
    path('api/', include('conversations.urls'))
]

if settings.DEBUG:
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=settings.BASE_DIR / 'frontend' / 'static',
    )

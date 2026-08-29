from rest_framework_simplejwt.views import TokenRefreshView
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from core.views import (
    RegistrationView,
    LoginView,
    forgot_password_view,
    verify_otp_view,
    reset_password_view,
    otp_generate,
    in_app_reset_password_view,
)
from frontend.views import (
    dashboard_view,
    login_view,
    registration_view,
    settings_view,
    reset_password_page_view,
)

urlpatterns = [
    path('', dashboard_view, name='dashboard'),
    path('signin/', login_view, name='frontend-login'),
    path('register/', registration_view, name='frontend-register'),
    path('reset-password/', reset_password_page_view, name='frontend-reset-password'),
    path('settings/', settings_view, name='frontend-settings'),
    path('admin/', admin.site.urls),
    path('registration/', RegistrationView.as_view(), name='registration'),
    path('login/', LoginView.as_view(), name='login'),
    path('forgot-password/', forgot_password_view, name='forgot-password'),
    path('verify-otp/', verify_otp_view, name='verify-otp'),
    path('reset-password-api/', reset_password_view, name='reset-password-api'),
    path('otp-generate/', otp_generate, name='otp-generate'),
    path('api/auth/forgot-password/', forgot_password_view, name='api-forgot-password'),
    path('api/auth/verify-otp/', verify_otp_view, name='api-verify-otp'),
    path('api/auth/reset-password/', reset_password_view, name='api-reset-password'),
    path('api/auth/otp-generate/', otp_generate, name='api-otp-generate'),
    path('api/auth/change-password/', in_app_reset_password_view, name='api-change-password'),
    path('change-password/', in_app_reset_password_view, name='change-password'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('api/', include('agent.urls')),
    path('api/', include('conversations.urls')),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=settings.BASE_DIR / 'frontend' / 'static',
    )

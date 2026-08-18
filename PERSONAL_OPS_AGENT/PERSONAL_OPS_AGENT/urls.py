from rest_framework_simplejwt.views import TokenRefreshView
from django.contrib import admin
from django.urls import include, path
from core.views import RegistrationView, LoginView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('registration/', RegistrationView.as_view()),
    path('login/', LoginView.as_view()),
    path('api/', include('agent.urls')),
    path('api/', include('conversations.urls'))
]

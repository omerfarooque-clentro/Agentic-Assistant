from django.shortcuts import render


def dashboard_view(request):
    return render(request, "frontend/dashboard.html")


def login_view(request):
    return render(request, "frontend/login.html")


def registration_view(request):
    return render(request, "frontend/register.html")


def settings_view(request):
    return render(request, "frontend/settings.html")

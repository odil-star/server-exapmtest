from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.http import JsonResponse
from django.urls import include, path

from quiz import views as quiz_views


def api_404_handler(request, exception):
    if request.path.startswith("/api/"):
        return JsonResponse(
            {"detail": "API endpoint не найден.", "path": request.path},
            status=404,
        )

    return JsonResponse({"detail": "Страница не найдена."}, status=404)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("quiz.api_urls")),
    path("", quiz_views.home, name="home"),
    path("tests/", quiz_views.test_list, name="test_list"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()

urlpatterns += [
    path("", include("quiz.urls")),
]

handler404 = api_404_handler

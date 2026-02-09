"""
URL configuration for auto-tuck-shop project.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

admin.site.site_header = "Auto Tuck Shop Admin"
admin.site.site_title = "Auto Tuck Shop Admin"
admin.site.index_title = "Dashboard"

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="home"),
    path("admin/", admin.site.urls),
    path("webhook/", include("apps.whatsapp.urls")),
]

if getattr(settings, "ENABLE_TEST_API", False):
    urlpatterns += [
        path("test/", include("apps.testing.urls")),
    ]

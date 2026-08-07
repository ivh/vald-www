"""
URL configuration for vald_web project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django_ratelimit.decorators import ratelimit

from vald.admin import admin_help

# Customize admin site
admin.site.site_header = "VALD admin"
admin.site.site_title = "VALD admin"
admin.site.index_title = "VALD administration"

# /admin/ is reachable from the public internet and Django's admin login has no
# throttle of its own, so staff passwords were guessable at full speed - while
# the VALD login next door is limited to 5/min. Wrapping the bound method is the
# cheap way in; a custom AdminSite would mean re-registering every ModelAdmin.
#
# Must happen before admin.site.urls is evaluated below, which is when
# get_urls() captures self.login.
#
# block=True unlike the site's own limits: there is no admin template in which
# to render a friendly "try again later", so a 403 is the honest answer.
admin.site.login = ratelimit(
    key='vald.ratelimit.client_ip',
    rate='vald.ratelimit.admin_login_rate',
    method='POST',
    block=True,
)(admin.site.login)

urlpatterns = [
    # Ahead of admin.site.urls so it resolves; it spans models, so it belongs to
    # no single ModelAdmin.
    path("admin/help/", admin_help, name="admin_help"),
    path("admin/", admin.site.urls),
    path("", include("vald.urls")),
]

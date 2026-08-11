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
from django.contrib.auth.models import Group
from django.contrib.auth.models import User as StaffUser
from django.urls import path, include
from django.views.generic import RedirectView
from django_ratelimit.decorators import ratelimit

from vald.admin import admin_help, admin_stats

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

# There is one shared staff login and no plans for per-person accounts, so the
# "Authentication and Authorization" box managed nothing and cost something: its
# "Users" is django.contrib.auth's, sitting in the sidebar directly above VALD's
# own "Users" under the same label, which is the confusion this whole codebase
# keeps having to warn about.
#
# Here rather than in vald/admin.py because vald precedes django.contrib.admin in
# INSTALLED_APPS, so vald.admin is imported before auth's ModelAdmins exist to be
# unregistered. By the time URLs load, autodiscovery has finished.
#
# Changing the staff password still works - admin:password_change is part of the
# AdminSite, not of this ModelAdmin. Adding or inspecting a staff account is now
# a shell job: manage.py createsuperuser / changepassword.
admin.site.unregister(StaffUser)
admin.site.unregister(Group)

urlpatterns = [
    # Ahead of admin.site.urls so they resolve; both span models, so they belong
    # to no single ModelAdmin.
    path("admin/help/", admin_help, name="admin_help"),
    path("admin/stats/", admin_stats, name="admin_stats"),

    # The index is an app list and nothing else, which the sidebar already shows
    # on every other page - and it is the one admin page with no sidebar at all,
    # because Django blanks that block there. Land on the user list instead,
    # which is what the admin is opened for.
    #
    # An exact "admin/" match, so only the index is shadowed and everything below
    # it still falls through to the include. pattern_name rather than a literal
    # URL because deployment sets FORCE_SCRIPT_NAME, so the admin is not at
    # /admin/ in production; reverse() picks the prefix up, a hardcoded path
    # would not. Temporary on purpose - a 301 would be cached by every browser
    # that ever hit it and outlive the decision.
    path("admin/", RedirectView.as_view(
        pattern_name="admin:vald_user_changelist", permanent=False)),

    path("admin/", admin.site.urls),
    path("", include("vald.urls")),
]

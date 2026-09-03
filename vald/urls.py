from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'vald'

urlpatterns = [
    # Main page
    path('', views.index, name='index'),

    # Authentication
    path('login/', views.login, name='login'),
    path('activate/<str:token>/', views.activate_account, name='activate_account'),
    path('set-password/', views.set_password, name='set_password'),
    path('reset-password/', views.request_password_reset, name='request_password_reset'),
    path('reset-password/<str:token>/', views.reset_password, name='reset_password'),

    # Forms
    path('extractall/', views.extractall, name='extractall'),
    path('extractelement/', views.extractelement, name='extractelement'),
    path('extractstellar/', views.extractstellar, name='extractstellar'),
    path('showline/', views.showline, name='showline'),
    path('showline-online/', views.showline_online, name='showline_online'),

    # Form submission
    path('submit/', views.submit_request, name='submit_request'),

    # Account details
    path('account/', views.account, name='account'),

    # Unit selection
    path('unitselection/', views.unitselection, name='unitselection'),
    path('save-units/', views.save_units, name='save_units'),

    # Personal configuration
    path('persconf/', views.persconf, name='persconf'),
    # Read-only view of one of the selectable system configurations
    path('config/<slug:slug>/', views.system_config, name='system_config'),

    # Request tracking
    path('my-requests/', views.my_requests, name='my_requests'),
    path('request/<uuid:uuid>/', views.request_detail, name='request_detail'),
    # Polled by the detail page while a job runs, in place of a meta refresh.
    path('request/<uuid:uuid>/status/', views.request_status, name='request_status'),
    # The filename forms are the canonical ones: wget names the saved file after
    # the last URL segment, so a link ending in "/download/" lands as
    # index.html. The bare forms redirect to them, which keeps links already
    # sent out by email working - and working correctly, since wget takes the
    # name from the final URL after redirects.
    path('request/<uuid:uuid>/download/', views.download_request, name='download_request'),
    path('request/<uuid:uuid>/download/<str:filename>', views.download_request, name='download_request'),
    path('request/<uuid:uuid>/download-bib/', views.download_bib_request, name='download_bib_request'),
    path('request/<uuid:uuid>/download-bib/<str:filename>', views.download_bib_request, name='download_bib_request'),
    # Machine-readable renderings, generated on first request from the ASCII
    # above. Same two URL forms, and for the same reason.
    # The bare form is where the format menu submits: it takes ?fmt= and
    # redirects into the path form, so the menu needs no JavaScript and wget
    # still names the saved file after the format.
    path('request/<uuid:uuid>/as/', views.download_converted, name='download_converted_pick'),
    path('request/<uuid:uuid>/as/<slug:fmt>/', views.download_converted, name='download_converted'),
    path('request/<uuid:uuid>/as/<slug:fmt>/<str:filename>', views.download_converted, name='download_converted'),

    # Info pages and news
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),

    # The doc/ file server served a documentation tree that the wiki replaced;
    # it is now old/documentation/ and nothing reads it. These two were its only
    # linked pages, so they are the only ones worth redirecting - the rest were
    # already unreachable. pattern_name, not a literal path, because deployment
    # sets FORCE_SCRIPT_NAME.
    path('doc/contact.html', RedirectView.as_view(
        pattern_name='vald:contact', permanent=True)),
    path('doc/about_vald.html', RedirectView.as_view(
        pattern_name='vald:about', permanent=True)),
    path('news/', views.news, name='news_all'),
    path('news/<int:newsitem>/', views.news, name='news'),
]

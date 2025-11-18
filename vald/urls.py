from django.urls import path
from . import views

app_name = 'vald'

urlpatterns = [
    # Main page
    path('', views.index, name='index'),

    # Authentication
    path('login/', views.login, name='login'),
    path('activate/', views.activate_account, name='activate_account'),
    path('set-password/', views.set_password, name='set_password'),

    # Forms
    path('extractall/', views.extractall, name='extractall'),
    path('extractelement/', views.extractelement, name='extractelement'),
    path('extractstellar/', views.extractstellar, name='extractstellar'),
    path('showline/', views.showline, name='showline'),
    path('showline-online/', views.showline_online, name='showline_online'),

    # Form submission
    path('submit/', views.submit_request, name='submit_request'),
    path('showline-online/submit/', views.showline_online_submit, name='showline_online_submit'),

    # Unit selection
    path('unitselection/', views.unitselection, name='unitselection'),
    path('save-units/', views.save_units, name='save_units'),

    # Personal configuration
    path('persconf/', views.persconf, name='persconf'),

    # Request tracking
    path('my-requests/', views.my_requests, name='my_requests'),
    path('request/<uuid:uuid>/', views.request_detail, name='request_detail'),
    path('request/<uuid:uuid>/download/', views.download_request, name='download_request'),
    path('request/<uuid:uuid>/download-bib/', views.download_bib_request, name='download_bib_request'),

    # Documentation and news
    path('doc/<str:docpage>', views.documentation, name='documentation'),
    path('news/<int:newsitem>/', views.news, name='news'),
]

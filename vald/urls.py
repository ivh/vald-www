from django.urls import path
from . import views

app_name = 'vald'

urlpatterns = [
    # Main page
    path('', views.index, name='index'),

    # Authentication
    path('login/', views.login, name='login'),

    # Forms
    path('extractall/', views.extractall, name='extractall'),
    path('extractelement/', views.extractelement, name='extractelement'),
    path('extractstellar/', views.extractstellar, name='extractstellar'),
    path('showline/', views.showline, name='showline'),
    path('showline-online/', views.showline_online, name='showline_online'),

    # Form submission
    path('submit/', views.submit_request, name='submit_request'),

    # Unit selection
    path('unitselection/', views.unitselection, name='unitselection'),
    path('save-units/', views.save_units, name='save_units'),

    # Personal configuration
    path('persconf/', views.persconf, name='persconf'),

    # Documentation and news
    path('doc/<str:docpage>', views.documentation, name='documentation'),
    path('news/<int:newsitem>/', views.news, name='news'),
]

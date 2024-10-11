from django.urls import path
from . import views

urlpatterns = [
    path('', views.passes.index, name='index'),
    path('ticket/<str:pk>/', views.passes.view_ticket, name='ticket'),
    path('ticket/<str:pk>/pkpass/', views.passes.ticket_pkpass, name='ticket_pkpass'),

    path('api/apple/v1/log', views.apple_api.log),
    path('api/apple/v1/devices/<str:device_id>/registrations/<str:pass_type_id>', views.apple_api.pass_status),
    path('api/apple/v1/devices/<str:device_id>/registrations/<str:pass_type_id>/<str:serial_number>', views.apple_api.registration),
    path('api/apple/v1/passes/<str:pass_type_id>/<str:serial_number>', views.apple_api.pass_document),

    path('account/', views.account.index, name='account'),
    path('account/db_login/', views.account.db_login, name='db_login'),
    path('account/db_login/login', views.account.db_login_start, name='db_login_start'),
    path('account/db_login/logout', views.account.db_logout, name='db_logout'),
    path('account/db_login/callback', views.account.db_login_callback, name='db_login_callback'),
]
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

    path('api/upload', views.api.upload_aztec),

    path('account/', views.account.index, name='account'),
    path('account/db/', views.account.db_account, name='db_account'),
    path('account/db_abo/', views.db_abo.view_db_abo, name='db_abo'),
    path('account/db_abo/new/', views.db_abo.new_abo, name='new_db_abo'),
    path('account/db_abo/abo/<abo_id>/delete/', views.db_abo.delete_abo, name='delete_db_abo'),
    path('account/db_login/', views.db.db_login, name='db_login'),
    path('account/db_login/login', views.db.db_login_start, name='db_login_start'),
    path('account/db_login/logout', views.db.db_logout, name='db_logout'),
    path('account/db_login/callback', views.db.db_login_callback, name='db_login_callback'),
    path('account/saarvv_login/', views.saarvv.saarvv_login, name='saarvv_login'),
    path('account/saarvv_login/logout/', views.saarvv.saarvv_logout, name='saarvv_logout'),
    path('account/saarvv/', views.saarvv.saarvv_account, name='saarvv_account'),
]
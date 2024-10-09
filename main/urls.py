from django.urls import path
from . import views, apple_api

urlpatterns = [
    path('', views.index, name='index'),
    path('ticket/<str:pk>/', views.view_ticket, name='ticket'),
    path('ticket/<str:pk>/pkpass/', views.ticket_pkpass, name='ticket_pkpass'),

    path('api/apple/v1/log', apple_api.log),
    path('api/apple/v1/devices/<str:device_id>/registrations/<str:pass_type_id>', apple_api.pass_status),
    path('api/apple/v1/devices/<str:device_id>/registrations/<str:pass_type_id>/<str:serial_number>', apple_api.registration),
    path('api/apple/v1/passes/<str:pass_type_id>/<str:serial_number>', apple_api.pass_document),
]
from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('ticket/<str:pk>/', views.view_ticket, name='ticket'),
    path('ticket/<str:pk>/pkpass/', views.ticket_pkpass, name='ticket_pkpass'),
]
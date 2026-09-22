from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('assignments/<int:pk>/', views.assignment_detail, name='assignment_detail'),
    path('assignments/<int:pk>/log/', views.log_session, name='log_session'),
    path('assignments/<int:pk>/goals/', views.achievements, name='achievements'),
    path('assignments/<int:pk>/stop/', views.stop_assignment, name='stop_assignment'),
    path('sessions/<int:pk>/edit/', views.session_edit, name='session_edit'),
    path('sessions/<int:pk>/delete/', views.session_delete, name='session_delete'),
    path('staff/reports/', views.staff_report, name='staff_report'),
    path('staff/reports/export/', views.export_csv, name='export_csv'),
    path('staff/reports/lock/', views.month_lock, name='month_lock'),
    path('staff/assignments/', views.staff_assignments, name='staff_assignments'),
    path('staff/assignments/<int:pk>/print/', views.print_assignment, name='print_assignment'),
]

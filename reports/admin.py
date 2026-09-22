from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Assignment, Goal, MonthLock, Session, Site, Student, StudentGoal
from .utils import is_locked

admin.site.site_header = 'LVAEP administration'
admin.site.site_title = 'LVAEP'
admin.site.index_title = 'Tutors, students & reporting'


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['name']
    list_filter = ['name']
    search_fields = ['name']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['last_name', 'first_name', 'created_at']
    list_filter = ['created_at']
    search_fields = ['first_name', 'last_name', 'notes']
    readonly_fields = ['created_at']


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ['student', 'tutor', 'site', 'meeting_days', 'meeting_time', 'start_date', 'status', 'stopped_on']
    list_filter = ['status', 'site', 'start_date']
    search_fields = ['student__first_name', 'student__last_name', 'tutor__username', 'tutor__first_name', 'tutor__last_name']
    autocomplete_fields = ['student', 'tutor', 'site']
    list_select_related = ['student', 'tutor', 'site']


class LockedFactAdmin(admin.ModelAdmin):
    actions = None

    def fact_date(self, obj):
        raise NotImplementedError

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (obj is None or not is_locked(self.fact_date(obj)))

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (obj is None or not is_locked(self.fact_date(obj)))


@admin.register(Session)
class SessionAdmin(LockedFactAdmin):
    list_display = ['date', 'assignment', 'status', 'hours', 'created_by']
    list_filter = ['status', 'date', 'assignment__site']
    search_fields = ['assignment__student__first_name', 'assignment__student__last_name', 'assignment__tutor__username', 'notes']
    autocomplete_fields = ['assignment']
    readonly_fields = ['created_by', 'created_at', 'updated_at']
    list_select_related = ['assignment__student', 'assignment__tutor', 'created_by']
    date_hierarchy = 'date'

    def fact_date(self, obj):
        return obj.date

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ['category', 'text', 'is_reportable', 'order']
    list_filter = ['category', 'is_reportable']
    search_fields = ['text']


@admin.register(StudentGoal)
class StudentGoalAdmin(LockedFactAdmin):
    list_display = ['student', '__str__', 'attained_on', 'recorded_by']
    list_filter = ['attained_on', 'goal__category', 'goal__is_reportable']
    search_fields = ['student__first_name', 'student__last_name', 'goal__text', 'custom_text']
    autocomplete_fields = ['student', 'goal']
    readonly_fields = ['recorded_by', 'created_at']
    list_select_related = ['student', 'goal', 'recorded_by']

    def fact_date(self, obj):
        return obj.attained_on

    def save_model(self, request, obj, form, change):
        if not change:
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(MonthLock)
class MonthLockAdmin(admin.ModelAdmin):
    list_display = ['year', 'month', 'locked_by', 'locked_at']
    list_filter = ['year', 'month']
    search_fields = ['locked_by__username']
    readonly_fields = ['locked_by', 'locked_at']
    list_select_related = ['locked_by']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.locked_by = request.user
        super().save_model(request, obj, form, change)

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Assignment, Session


def assignments_for_user(user):
    qs = Assignment.objects.select_related('student', 'site', 'tutor')
    return qs if user.is_staff else qs.filter(tutor=user)


def get_assignment_for_user(user, pk):
    return get_object_or_404(assignments_for_user(user), pk=pk)


def get_session_for_user(user, pk):
    qs = Session.objects.select_related('assignment__student', 'assignment__tutor', 'assignment__site')
    if not user.is_staff:
        qs = qs.filter(assignment__tutor=user)
    return get_object_or_404(qs, pk=pk)


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped

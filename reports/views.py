import csv
import logging
from datetime import date, timedelta
from decimal import Decimal
from smtplib import SMTPException

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Q, Subquery, Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import assignments_for_user, get_assignment_for_user, get_session_for_user, staff_required
from .forms import AchievementForm, OtherGoalForm, SessionForm, StopForm
from .models import Assignment, Goal, MonthLock, Session, Site, StudentGoal
from .reporting import monthly_report
from .utils import default_report_month, fiscal_grid, fiscal_year_for, is_locked, month_bounds

logger = logging.getLogger(__name__)


def add_validation_errors(form, error):
    if hasattr(error, 'message_dict'):
        for field, errors in error.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, error)


def selected_fy(request):
    try:
        year = int(request.GET.get('fy', fiscal_year_for(timezone.localdate())))
        if not 1900 <= year <= 9998:
            raise ValueError
        return year
    except (ValueError, TypeError):
        return None


def selected_month(request):
    value = request.GET.get('month', default_report_month().strftime('%Y-%m'))
    try:
        parsed = date.fromisoformat(value + '-01')
        if parsed.year < 1900 or value != parsed.strftime('%Y-%m'):
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        return None


def dashboard_context(user, bound_form=None, assignment_id=None):
    today = timezone.localdate()
    month_start, month_end = month_bounds(today.year, today.month)
    last_held = Session.objects.filter(assignment=OuterRef('pk'), status='HELD').order_by('-date').values('hours')[:1]
    assignments = assignments_for_user(user).filter(status='ACTIVE').annotate(
        last_hours=Subquery(last_held),
        month_hours=Sum('sessions__hours', filter=Q(sessions__date__range=(month_start, month_end))),
    )
    first_day = today - timedelta(days=13)
    logged = set(Session.objects.filter(assignment__in=assignments, date__range=(first_day, today)).values_list('assignment_id', 'date'))
    locks = set(MonthLock.objects.values_list('year', 'month'))
    cards = []
    for assignment in assignments:
        hours = assignment.last_hours or Decimal('1.5')
        prompts = []
        for offset in range(14):
            day = first_day + timedelta(days=offset)
            if day >= assignment.start_date and day.weekday() in assignment.meeting_weekdays() and (assignment.pk, day) not in logged and (day.year, day.month) not in locks:
                prompts.append(day)
        form = bound_form if assignment.pk == assignment_id else SessionForm(
            prefix=f'a{assignment.pk}', initial={'date': today, 'status': 'HELD', 'hours': hours})
        cards.append({'assignment': assignment, 'form': form, 'hours': hours, 'prompts': prompts})
    return {'cards': cards, 'today': today, 'stopped_assignments': assignments_for_user(user).filter(status='STOPPED')}


@login_required
def dashboard(request):
    return render(request, 'reports/dashboard.html', dashboard_context(request.user))


@login_required
@require_POST
def log_session(request, pk):
    assignment = get_assignment_for_user(request.user, pk)
    prefix = f'a{pk}' if request.POST.get('quick_log') else None
    form = SessionForm(request.POST, prefix=prefix, instance=Session(assignment=assignment, created_by=request.user))
    if form.is_valid():
        try:
            with transaction.atomic():
                form.save()
            messages.success(request, f'Session recorded for {assignment.student}.')
            return redirect('dashboard')
        except ValidationError as error:
            add_validation_errors(form, error)
        except IntegrityError:
            form.add_error(None, 'A session already exists for this pairing and date. Please refresh.')
    if prefix and assignment.status == 'ACTIVE':
        return render(request, 'reports/dashboard.html', dashboard_context(request.user, form, pk), status=400)
    return render(request, 'reports/session_form.html', {'form': form, 'assignment': assignment, 'creating': True}, status=400)


@login_required
def assignment_detail(request, pk):
    assignment = get_assignment_for_user(request.user, pk)
    year = selected_fy(request)
    if year is None:
        return HttpResponseBadRequest('Invalid fiscal year.')
    context = fiscal_grid(assignment, year)
    context.update(assignment=assignment)
    return render(request, 'reports/assignment_detail.html', context)


@login_required
def session_edit(request, pk):
    session = get_session_for_user(request.user, pk)
    form = SessionForm(request.POST or None, instance=session)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
            messages.success(request, 'Session updated.')
            return redirect('assignment_detail', pk=session.assignment_id)
        except ValidationError as error:
            add_validation_errors(form, error)
        except IntegrityError:
            form.add_error(None, 'A session already exists for this pairing and date.')
    return render(request, 'reports/session_form.html', {'form': form, 'assignment': session.assignment, 'session': session, 'locked': is_locked(session.date)})


@login_required
def session_delete(request, pk):
    session = get_session_for_user(request.user, pk)
    if request.method == 'POST':
        try:
            session.delete()
            messages.success(request, 'Session deleted.')
            return redirect('assignment_detail', pk=session.assignment_id)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
    return render(request, 'reports/session_delete.html', {'session': session, 'locked': is_locked(session.date)})


@login_required
def achievements(request, pk):
    assignment = get_assignment_for_user(request.user, pk)
    action = request.POST.get('action')
    form = AchievementForm(request.POST if action == 'checklist' else None, student=assignment.student)
    other_form = OtherGoalForm(request.POST if action == 'other' else None, instance=StudentGoal(student=assignment.student, recorded_by=request.user))
    if request.method == 'POST':
        if action == 'checklist' and form.is_valid():
            try:
                with transaction.atomic():
                    for goal in form.catalog:
                        original = form.existing.get(goal.pk)
                        checked, attained = form.cleaned_data[f'goal_{goal.pk}'], form.cleaned_data.get(f'date_{goal.pk}')
                        if original and is_locked(original.attained_on):
                            continue
                        if checked and not original:
                            StudentGoal.objects.create(student=assignment.student, goal=goal, attained_on=attained, recorded_by=request.user)
                        elif checked and original and original.attained_on != attained:
                            original.attained_on = attained
                            original.save()
                        elif not checked and original:
                            original.delete()
                messages.success(request, 'Achievements updated.')
                return redirect('achievements', pk=pk)
            except ValidationError as error:
                form.add_error(None, ' '.join(error.messages))
            except IntegrityError:
                form.add_error(None, 'Achievements changed while you were editing. Please refresh.')
        elif action == 'other' and other_form.is_valid():
            try:
                other_form.save()
                messages.success(request, 'Other achievement added.')
                return redirect('achievements', pk=pk)
            except ValidationError as error:
                add_validation_errors(other_form, error)
        elif action == 'remove_other':
            achievement = get_object_or_404(assignment.student.achievements, pk=request.POST.get('achievement_id'), goal__isnull=True)
            try:
                achievement.delete()
                messages.success(request, 'Other achievement removed.')
            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))
            return redirect('achievements', pk=pk)
        elif action not in ('checklist', 'other'):
            return HttpResponseBadRequest('Unknown achievement action.')
    others = list(assignment.student.achievements.filter(goal__isnull=True))
    for attainment in others:
        attainment.month_locked = is_locked(attainment.attained_on)
    return render(request, 'reports/achievements.html', {'assignment': assignment, 'form': form, 'other_form': other_form, 'others': others})


@login_required
def stop_assignment(request, pk):
    assignment = get_assignment_for_user(request.user, pk)
    if assignment.status == 'STOPPED':
        messages.info(request, 'This pairing is already stopped.')
        return redirect('assignment_detail', pk=pk)
    form = StopForm(request.POST or None, instance=assignment)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except ValidationError as error:
            add_validation_errors(form, error)
        else:
            recipients = list(get_user_model().objects.filter(is_staff=True).exclude(email='').values_list('email', flat=True))
            try:
                if recipients:
                    send_mail(f'Tutoring stopped: {assignment.student}',
                              f'Tutor: {assignment.tutor.get_full_name() or assignment.tutor.username}\nStudent: {assignment.student}\nSite: {assignment.site}\nStopped on: {assignment.stopped_on}\nReason: {assignment.stopped_reason}',
                              None, recipients)
                    messages.success(request, 'Tutoring marked as stopped. Staff have been notified by email.')
                else:
                    messages.warning(request, 'Tutoring marked as stopped. No staff email addresses are configured; please contact the office.')
            except (SMTPException, OSError):
                logger.exception('Unable to send stopped-pairing notification')
                messages.warning(request, 'Tutoring marked as stopped, but the staff email failed. Please contact the office.')
            return redirect('assignment_detail', pk=pk)
    return render(request, 'reports/stop.html', {'assignment': assignment, 'form': form})


@staff_required
def staff_report(request):
    month = selected_month(request)
    if month is None:
        return HttpResponseBadRequest('Invalid month. Use YYYY-MM.')
    context = monthly_report(month.year, month.month)
    context.update(month=month, month_value=month.strftime('%Y-%m'), lock=MonthLock.objects.filter(year=month.year, month=month.month).select_related('locked_by').first())
    return render(request, 'reports/staff_report.html', context)


def csv_safe(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text


@staff_required
def export_csv(request):
    month = selected_month(request)
    if month is None:
        return HttpResponseBadRequest('Invalid month. Use YYYY-MM.')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="tutor-sessions-{month:%Y-%m}.csv"'
    writer = csv.writer(response)
    writer.writerow(['date', 'site', 'tutor', 'student', 'status', 'hours', 'notes'])
    sessions = Session.objects.filter(date__range=month_bounds(month.year, month.month)).select_related('assignment__site', 'assignment__tutor', 'assignment__student').order_by('date', 'pk')
    for session in sessions.iterator():
        assignment = session.assignment
        writer.writerow([session.date.isoformat(), csv_safe(assignment.site), csv_safe(assignment.tutor.get_full_name() or assignment.tutor.username), csv_safe(assignment.student), session.status, f'{session.hours:.2f}', csv_safe(session.notes)])
    return response


@staff_required
@require_POST
def month_lock(request):
    month = selected_month(request)
    if month is None:
        return HttpResponseBadRequest('Invalid month. Use YYYY-MM.')
    if request.POST.get('action') == 'lock':
        MonthLock.objects.get_or_create(year=month.year, month=month.month, defaults={'locked_by': request.user})
        messages.success(request, f'{month:%B %Y} locked. Sessions and achievements are protected.')
    elif request.POST.get('action') == 'unlock':
        MonthLock.objects.filter(year=month.year, month=month.month).delete()
        messages.success(request, f'{month:%B %Y} unlocked.')
    else:
        return HttpResponseBadRequest('Unknown lock action.')
    return redirect(reverse('staff_report') + f'?month={month:%Y-%m}')


@staff_required
def staff_assignments(request):
    assignments = assignments_for_user(request.user)
    site = request.GET.get('site', '')
    status = request.GET.get('status', '')
    query = request.GET.get('q', '').strip()
    if site:
        if not site.isdigit():
            return HttpResponseBadRequest('Invalid site.')
        assignments = assignments.filter(site_id=site)
    if status in ('ACTIVE', 'STOPPED'):
        assignments = assignments.filter(status=status)
    if query:
        for term in query.split():
            assignments = assignments.filter(Q(tutor__first_name__icontains=term) | Q(tutor__last_name__icontains=term) | Q(tutor__username__icontains=term))
    return render(request, 'reports/staff_assignments.html', {'assignments': assignments, 'sites': Site.objects.all(), 'selected_site': site, 'selected_status': status, 'query': query})


@staff_required
def print_assignment(request, pk):
    assignment = get_assignment_for_user(request.user, pk)
    year = selected_fy(request)
    if year is None:
        return HttpResponseBadRequest('Invalid fiscal year.')
    context = fiscal_grid(assignment, year)
    attained = assignment.student.achievements.filter(attained_on__range=(date(year, 7, 1), date(year + 1, 6, 30))).select_related('goal')
    by_goal = {a.goal_id: a for a in attained if a.goal_id}
    groups = []
    for category, label in Goal.Category.choices:
        groups.append({'label': label, 'rows': [{'goal': g, 'attainment': by_goal.get(g.pk)} for g in Goal.objects.filter(category=category)]})
    context.update(assignment=assignment, achievement_groups=groups, other_goals=[a for a in attained if not a.goal_id], stopped_in_fy=bool(assignment.stopped_on and date(year, 7, 1) <= assignment.stopped_on <= date(year + 1, 6, 30)))
    return render(request, 'reports/print_assignment.html', context)

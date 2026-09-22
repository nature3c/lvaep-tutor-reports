from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import Assignment, Session, StudentGoal
from .utils import month_bounds


def metrics():
    return {
        'hours': Coalesce(Sum('hours'), Value(Decimal('0')), output_field=DecimalField(max_digits=12, decimal_places=2)),
        'held': Count('pk', filter=Q(status='HELD')),
        'ta': Count('pk', filter=Q(status='TA')),
        'sa': Count('pk', filter=Q(status='SA')),
        'holiday': Count('pk', filter=Q(status='H')),
    }


def monthly_report(year, month):
    start, end = month_bounds(year, month)
    sessions = Session.objects.filter(date__range=(start, end))
    summary = sessions.aggregate(**metrics(), students=Count('assignment__student', filter=Q(status='HELD'), distinct=True))
    pairings = list(Assignment.objects.filter(sessions__date__range=(start, end)).select_related('student', 'site', 'tutor').distinct())
    tables = []
    for dimension, fields in [
        ('site', ['assignment__site_id', 'assignment__site__name']),
        ('tutor', ['assignment__tutor_id', 'assignment__tutor__first_name', 'assignment__tutor__last_name', 'assignment__tutor__username']),
        ('student', ['assignment__student_id', 'assignment__student__first_name', 'assignment__student__last_name']),
    ]:
        rows = list(sessions.order_by().values(*fields).annotate(**metrics()).order_by(*fields[1:]))
        for row in rows:
            if dimension == 'site':
                row['label'] = row['assignment__site__name']
            else:
                row['label'] = f"{row[f'assignment__{dimension}__first_name']} {row[f'assignment__{dimension}__last_name']}".strip()
                if not row['label'] and dimension == 'tutor':
                    row['label'] = row['assignment__tutor__username']
            row['assignments'] = [a for a in pairings if getattr(a, f'{dimension}_id') == row[f'assignment__{dimension}_id']]
        tables.append({'label': dimension.title(), 'rows': rows})
    missing = Assignment.objects.filter(status='ACTIVE', start_date__lte=end).exclude(
        sessions__date__range=(start, end)).select_related('student', 'tutor', 'site')
    stopped = Assignment.objects.filter(status='STOPPED', stopped_on__range=(start, end)).select_related('student', 'tutor', 'site')
    goals = StudentGoal.objects.filter(attained_on__range=(start, end)).select_related('student', 'goal', 'recorded_by')
    return {'summary': summary, 'tables': tables, 'missing': missing, 'stopped': stopped, 'attained_goals': goals}

import calendar
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone


def fiscal_year_for(day):
    return day.year if day.month >= 7 else day.year - 1


def fy_months(start_year):
    return [(start_year, month) for month in range(7, 13)] + [(start_year + 1, month) for month in range(1, 7)]


def is_locked(day):
    from .models import MonthLock
    return MonthLock.objects.filter(year=day.year, month=day.month).exists()


def month_bounds(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def default_report_month():
    today = timezone.localdate()
    if today.day <= 7:
        from datetime import timedelta
        return today.replace(day=1) - timedelta(days=1)
    return today


def fiscal_grid(assignment, year):
    """The grid is a projection of session facts; totals stay in the database."""
    from .models import MonthLock
    from django.db.models.functions import ExtractMonth, ExtractYear
    months = fy_months(year)
    sessions = assignment.sessions.filter(date__gte=date(year, 7, 1), date__lte=date(year + 1, 6, 30))
    by_date = {s.date: s for s in sessions}
    locked = set(MonthLock.objects.filter(year__in=[year, year + 1]).values_list('year', 'month'))
    totals = {(r['y'], r['m']): r['total'] for r in sessions.annotate(
        y=ExtractYear('date'), m=ExtractMonth('date')).values('y', 'm').annotate(total=Sum('hours'))}
    rows = []
    for day in range(1, 32):
        cells = []
        for y, m in months:
            valid = day <= calendar.monthrange(y, m)[1]
            session = by_date.get(date(y, m, day)) if valid else None
            cells.append({'valid': valid, 'session': session, 'locked': (y, m) in locked})
        rows.append({'day': day, 'cells': cells})
    session_list = sorted(by_date.values(), key=lambda s: s.date, reverse=True)
    for session in session_list:
        session.month_locked = (session.date.year, session.date.month) in locked
    return {
        'fy': year, 'fy_end': year + 1, 'grid_rows': rows,
        'grid_months': [{'label': calendar.month_abbr[m], 'year': y, 'month': m,
                         'total': totals.get((y, m), Decimal('0')), 'locked': (y, m) in locked} for y, m in months],
        'fy_total': sessions.aggregate(total=Sum('hours'))['total'] or Decimal('0'),
        'sessions': session_list,
    }

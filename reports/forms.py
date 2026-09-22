from django import forms
from django.utils import timezone

from .models import Assignment, Goal, Session, StudentGoal
from .utils import is_locked


class DateInput(forms.DateInput):
    input_type = 'date'


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ['date', 'status', 'hours', 'notes']
        widgets = {
            'date': DateInput(),
            'hours': forms.NumberInput(attrs={'min': '0', 'max': '8', 'step': '0.25', 'inputmode': 'decimal'}),
            'notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional session notes'}),
        }
        help_texts = {'hours': 'Held: 0.25–8 hours. Absences and holidays are saved as 0 hours.'}

    def clean(self):
        data = super().clean()
        if data.get('status') in ('TA', 'SA', 'H'):
            data['hours'] = 0
            # Choosing an absence never requires changing the suggested held hours.
            self._errors.pop('hours', None)
        return data


class StopForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ['stopped_on', 'stopped_reason']
        labels = {'stopped_on': 'Last day of tutoring', 'stopped_reason': 'Reason for stopping'}
        widgets = {'stopped_on': DateInput(), 'stopped_reason': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['stopped_on'].required = True
        self.fields['stopped_reason'].required = True
        self.fields['stopped_on'].initial = timezone.localdate
        self.instance.status = Assignment.Status.STOPPED


class AchievementForm(forms.Form):
    """A dated checkbox for each catalog goal; unchanged locked facts stay readable."""
    def __init__(self, *args, student, **kwargs):
        super().__init__(*args, **kwargs)
        self.catalog = list(Goal.objects.all())
        self.existing = {a.goal_id: a for a in student.achievements.filter(goal__isnull=False)}
        self.groups = []
        for category, label in Goal.Category.choices:
            rows = []
            for goal in self.catalog:
                if goal.category != category:
                    continue
                attainment = self.existing.get(goal.pk)
                locked = bool(attainment and is_locked(attainment.attained_on))
                key, date_key = f'goal_{goal.pk}', f'date_{goal.pk}'
                self.fields[key] = forms.BooleanField(required=False, initial=bool(attainment), disabled=locked, label=goal.text)
                self.fields[date_key] = forms.DateField(required=False, initial=attainment.attained_on if attainment else timezone.localdate(), widget=DateInput(), disabled=locked, label=f'Date attained: {goal.text}')
                rows.append({'goal': goal, 'check': self[key], 'date': self[date_key], 'locked': locked})
            if rows:
                self.groups.append({'label': label, 'rows': rows})

    def clean(self):
        data = super().clean()
        for goal in self.catalog:
            key, date_key = f'goal_{goal.pk}', f'date_{goal.pk}'
            original = self.existing.get(goal.pk)
            if original and is_locked(original.attained_on):
                continue
            if data.get(key):
                day = data.get(date_key)
                if not day:
                    self.add_error(date_key, 'Enter the date attained.')
                elif day > timezone.localdate():
                    self.add_error(date_key, 'An achievement cannot be in the future.')
                elif is_locked(day):
                    self.add_error(date_key, 'This month is locked.')
        return data


class OtherGoalForm(forms.ModelForm):
    class Meta:
        model = StudentGoal
        fields = ['custom_text', 'attained_on']
        labels = {'custom_text': 'Other achievement', 'attained_on': 'Date attained'}
        widgets = {'attained_on': DateInput()}

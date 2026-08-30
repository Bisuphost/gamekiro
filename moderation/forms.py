from django import forms

from .models import Report


class ReportForm(forms.Form):
    reason = forms.ChoiceField(choices=Report.Reason.choices)
    detail = forms.CharField(widget=forms.Textarea, required=False, max_length=1000)

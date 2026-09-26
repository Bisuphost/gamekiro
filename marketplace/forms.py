from django import forms

from .models import Product


class ImportKeysForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.select_related("game"))
    keys_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 12, "placeholder": "One key per line"})
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def cleaned_keys(self):
        raw = self.cleaned_data["keys_text"]
        return [line.strip() for line in raw.splitlines() if line.strip()]

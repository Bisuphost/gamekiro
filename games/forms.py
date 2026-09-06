from django import forms

from .models import Review


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["rating", "body"]
        widgets = {
            "rating": forms.Select(choices=[(1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5")]),
            "body": forms.Textarea(attrs={"rows": 5, "placeholder": "Share your thoughts..."}),
        }
        labels = {"rating": "Rating", "body": "Review"}

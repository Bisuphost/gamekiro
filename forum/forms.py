from django import forms

from .models import Post


class ThreadForm(forms.Form):
    title = forms.CharField(max_length=255)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}
        labels = {"body": "Reply"}

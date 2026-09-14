import bleach
from django import forms

from .models import Category, Post, PostImage
from .services import sanitize_post_body


def clean_rich_text_body(raw_html):
    cleaned = sanitize_post_body(raw_html)
    has_embed = "<img" in cleaned or "<iframe" in cleaned
    text_only = bleach.clean(cleaned, tags=[], attributes={}, strip=True).strip()
    if not text_only and not has_embed:
        raise forms.ValidationError("This field cannot be empty.")
    return cleaned


class ThreadForm(forms.Form):
    title = forms.CharField(max_length=255)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))

    def clean_body(self):
        return clean_rich_text_body(self.cleaned_data.get("body", ""))


class PostImageUploadForm(forms.ModelForm):
    class Meta:
        model = PostImage
        fields = ["image"]


class ThreadCreateAnyForm(ThreadForm):
    category = forms.ModelChoiceField(queryset=Category.objects.all())

    field_order = ["category", "title", "body"]


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}
        labels = {"body": "Reply"}

    def clean_body(self):
        return clean_rich_text_body(self.cleaned_data.get("body", ""))

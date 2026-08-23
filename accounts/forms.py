from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class SignupForm(UserCreationForm):
    email = User._meta.get_field("email").formfield(required=True)

    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]

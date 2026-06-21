from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import UserProfile

class LoginForm(forms.Form):
    email    = forms.EmailField(label='Email', widget=forms.EmailInput(attrs={'placeholder': 'ton@email.com'}))
    password = forms.CharField(label='Mot de passe', widget=forms.PasswordInput(attrs={'placeholder': '••••••••'}))

class SignupStep1Form(forms.Form):
    first_name = forms.CharField(max_length=100, label='Prénom', widget=forms.TextInput(attrs={'placeholder': 'Prénom'}))
    last_name  = forms.CharField(max_length=100, label='Nom', widget=forms.TextInput(attrs={'placeholder': 'Nom'}))
    email      = forms.EmailField(label='Email', widget=forms.EmailInput(attrs={'placeholder': 'ton@email.com'}))
    password   = forms.CharField(label='Mot de passe', min_length=6, widget=forms.PasswordInput(attrs={'placeholder': '••••••••'}))
    school     = forms.CharField(max_length=200, required=False, label='École', widget=forms.TextInput(attrs={'placeholder': 'Ton lycée (optionnel)'}))

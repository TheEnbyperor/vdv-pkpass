from django import forms

from crispy_forms_gds.helper import FormHelper
from crispy_forms_gds.layout import Submit


class TicketUploadForm(forms.Form):
    ticket = forms.FileField(
        label="Your ticket",
        error_messages={
            "required": "Please upload a ticket image or PDF",
        }
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(Submit("submit", "Upload"))


class SaarVVLoginForm(forms.Form):
    username = forms.CharField(label="Email/Username", required=True)
    password = forms.CharField(label="Password", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(Submit("submit", "Login"))


class DBAboForm(forms.Form):
    subscription_number = forms.CharField(label="Subscription Number", required=True)
    surname = forms.CharField(label="Surname", required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(Submit("submit", "Add"))
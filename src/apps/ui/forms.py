"""Form helpers shared by module screens."""
from django import forms


class BootstrapFormMixin:
    """Gives widgets Bootstrap 5 classes so server-rendered forms look consistent."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                cls = "form-check-input"
            elif isinstance(w, forms.Select | forms.SelectMultiple):
                cls = "form-select"
            elif isinstance(w, forms.CheckboxSelectMultiple | forms.RadioSelect | forms.FileInput):
                cls = "form-control" if isinstance(w, forms.FileInput) else ""
            else:
                cls = "form-control"
            if cls:
                w.attrs["class"] = f"{w.attrs.get('class', '')} {cls}".strip()

    def add_error(self, field, error):
        super().add_error(field, error)
        if field and field in self.fields:
            self._mark_field_invalid(field)

    def full_clean(self):
        super().full_clean()
        for field_name in self.errors:
            if field_name in self.fields:
                self._mark_field_invalid(field_name)

    def _mark_field_invalid(self, field_name):
        w = self.fields[field_name].widget
        existing = w.attrs.get("class", "")
        classes = existing.split()
        if "is-invalid" not in classes:
            classes.append("is-invalid")
            w.attrs["class"] = " ".join(classes)


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=300, widget=forms.TextInput(attrs={"placeholder": "Reason (required)"}))


class ClientOnboardingForm(BootstrapFormMixin, forms.Form):
    full_name = forms.CharField(max_length=150, label="Full name", required=True)
    email = forms.EmailField(label="Work email", required=True)
    phone = forms.CharField(max_length=30, label="Phone number", required=False)
    company_name = forms.CharField(max_length=200, label="Company / Organization", required=True)
    facility_address = forms.CharField(max_length=300, label="Facility address", required=False)
    service_scope = forms.CharField(
        max_length=500, label="Asset / Service scope",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Describe assets or maintenance services requested..."}),
        required=False,
    )
    terms_accepted = forms.BooleanField(
        label="I accept the enterprise platform terms and data protection policy",
        required=True,
    )

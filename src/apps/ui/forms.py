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


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=300, widget=forms.TextInput(attrs={"placeholder": "Reason (required)"}))

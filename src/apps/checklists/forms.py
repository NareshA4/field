from django import forms

from apps.ui.forms import BootstrapFormMixin
from apps.workorders.models import WorkOrder

from .models import ChecklistItem


class TemplateForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=150)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    work_type = forms.ChoiceField(choices=[("", "Any type of work")] + list(WorkOrder.WorkType.choices),
                                  required=False, label="Applies to")
    is_required = forms.BooleanField(
        required=False, label="Required",
        help_text="Matching work orders cannot be completed or closed until this checklist is completed.")


class ItemForm(BootstrapFormMixin, forms.Form):
    prompt = forms.CharField(max_length=300, label="Question")
    item_type = forms.ChoiceField(choices=ChecklistItem.ItemType.choices, label="Answer type")
    guidance = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Guidance for the technician")
    required = forms.BooleanField(required=False, initial=True, label="Answer required")
    options = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                              help_text="Selection only: one option per line.")
    exception_options = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                        label="Exception options",
                                        help_text="Selection only: options (one per line) that need a finding.")
    min_value = forms.CharField(required=False, label="Minimum", help_text="Number only. Outside the range = exception.")
    max_value = forms.CharField(required=False, label="Maximum")
    unit = forms.CharField(max_length=20, required=False)
    evidence_required = forms.BooleanField(required=False, label="Evidence (photo / file) required")

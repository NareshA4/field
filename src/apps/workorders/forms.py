from django import forms

from apps.assets.models import Asset
from apps.incidents.forms import EvidenceForm, dt_field  # noqa: F401  (re-exported for the views)
from apps.tenancy.models import Membership
from apps.ui.forms import BootstrapFormMixin

from .models import WorkOrder


class _AssetChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.asset_tag} · {obj.name} ({obj.site.code})"


class _MemberChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.user.display_name


class WorkOrderForm(BootstrapFormMixin, forms.Form):
    asset = _AssetChoice(queryset=Asset.objects.none())
    title = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    work_type = forms.ChoiceField(choices=WorkOrder.WorkType.choices)
    priority = forms.ChoiceField(choices=WorkOrder.Priority.choices, initial="MEDIUM")
    planned_start = dt_field("Planned start")
    planned_end = dt_field("Planned end")
    estimated_hours = forms.DecimalField(required=False, min_value=0, max_digits=6, decimal_places=2)

    def __init__(self, *args, assets=None, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            del self.fields["asset"]
        else:
            self.fields["asset"].queryset = assets.exclude(status__in=["RETIRED", "DISPOSED"]).order_by("asset_tag")


class PlanForm(BootstrapFormMixin, forms.Form):
    planned_start = dt_field("Planned start", required=True)
    planned_end = dt_field("Planned end", required=True)
    estimated_hours = forms.DecimalField(required=False, min_value=0, max_digits=6, decimal_places=2)
    priority = forms.ChoiceField(choices=WorkOrder.Priority.choices)


class TechnicianForm(BootstrapFormMixin, forms.Form):
    technician = _MemberChoice(queryset=Membership.objects.none(), empty_label="Choose a technician")
    reason = forms.CharField(max_length=500, required=False)

    def __init__(self, *args, technicians=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["technician"].queryset = technicians if technicians is not None else Membership.objects.none()


class CompleteForm(BootstrapFormMixin, forms.Form):
    resolution_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), min_length=10,
                                       label="Resolution notes")


class LaborForm(BootstrapFormMixin, forms.Form):
    technician = _MemberChoice(queryset=Membership.objects.none(), required=False, empty_label="Me")
    work_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    hours = forms.DecimalField(max_digits=5, decimal_places=2, min_value=0.01, max_value=24)
    notes = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, technicians=None, **kwargs):
        super().__init__(*args, **kwargs)
        if technicians is None:
            del self.fields["technician"]
        else:
            self.fields["technician"].queryset = technicians


class MaterialForm(BootstrapFormMixin, forms.Form):
    description = forms.CharField(max_length=200)
    part_number = forms.CharField(max_length=60, required=False)
    quantity = forms.DecimalField(max_digits=10, decimal_places=3, min_value=0.001)
    unit = forms.CharField(max_length=20, initial="pcs", required=False)

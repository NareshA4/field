from django import forms

from apps.assets.models import Asset, AssetMeter
from apps.ui.forms import BootstrapFormMixin

from .models import MaintenanceSchedule
from .services import PRIORITIES


class _AssetChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.asset_tag} · {obj.name} ({obj.site.code})"


class _MeterChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} ({obj.unit})"


class PlanForm(BootstrapFormMixin, forms.Form):
    asset = _AssetChoice(queryset=Asset.objects.none(), label="Asset")
    name = forms.CharField(max_length=150)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    priority = forms.ChoiceField(choices=[(p, p.title()) for p in PRIORITIES], initial="MEDIUM")
    estimated_hours = forms.DecimalField(required=False, min_value=0, max_digits=6, decimal_places=2)
    checklist_key = forms.ChoiceField(required=False, label="Required checklist (M08)")

    def __init__(self, *args, assets=None, checklist_keys=(), editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["checklist_key"].choices = [("", "None")] + list(checklist_keys)
        if editing:
            del self.fields["asset"]
        else:
            self.fields["asset"].queryset = assets.exclude(status__in=["RETIRED", "DISPOSED"]).order_by("asset_tag")


class ScheduleForm(BootstrapFormMixin, forms.Form):
    trigger_type = forms.ChoiceField(choices=MaintenanceSchedule.Trigger.choices, label="Based on")
    frequency = forms.ChoiceField(required=False, choices=[("", "-")] + MaintenanceSchedule.Frequency.choices,
                                  label="Repeats every ... (time-based)")
    interval_count = forms.IntegerField(required=False, min_value=1, max_value=1000, label="Interval (time-based)")
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                 label="First due date (time-based, site date)")
    meter = _MeterChoice(queryset=AssetMeter.objects.none(), required=False, label="Meter (meter-based)")
    interval_value = forms.DecimalField(required=False, min_value=0, max_digits=16, decimal_places=3,
                                        label="Every ... meter units (meter-based)")
    start_value = forms.DecimalField(required=False, min_value=0, max_digits=16, decimal_places=3, initial=0,
                                     label="Counting starts at (meter-based)")
    lead_days = forms.IntegerField(min_value=0, max_value=60, initial=0, label="Generate the work order ... days early")
    window_start_time = forms.TimeField(initial="08:00", widget=forms.TimeInput(attrs={"type": "time"}),
                                        label="Maintenance window starts at (site time)")
    window_hours = forms.IntegerField(min_value=1, max_value=72, initial=8, label="Window length (hours)")
    reminder_days = forms.IntegerField(min_value=0, max_value=60, initial=0, label="Remind planners ... days before")

    def __init__(self, *args, meters=None, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["meter"].queryset = meters if meters is not None else AssetMeter.objects.none()
        if editing:
            del self.fields["trigger_type"]

from django import forms

from apps.assets.models import Asset
from apps.ui.forms import BootstrapFormMixin

from .models import ServiceRequest

DT_FORMAT = "%Y-%m-%dT%H:%M"


def dt_field(label, required=False, **kw):
    return forms.DateTimeField(required=required, label=label, input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"],
                               widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DT_FORMAT), **kw)


class _AssetChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.asset_tag} · {obj.name} ({obj.site.code})"


class RequestForm(BootstrapFormMixin, forms.Form):
    asset = _AssetChoice(queryset=Asset.objects.none(), label="Asset")
    kind = forms.ChoiceField(choices=ServiceRequest.Kind.choices, label="Type")
    title = forms.CharField(max_length=200, help_text="What failed or what is needed?")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    severity = forms.ChoiceField(choices=ServiceRequest.Severity.choices, initial="MEDIUM")
    service_impact = forms.ChoiceField(choices=ServiceRequest.Impact.choices, label="Service impact")
    impact_notes = forms.CharField(max_length=500, required=False, label="Impact notes")
    occurred_at = dt_field("When did it happen? (blank = now)")
    downtime_started_at = dt_field("Downtime started (if the asset is down)")

    def __init__(self, *args, assets=None, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            del self.fields["asset"], self.fields["downtime_started_at"], self.fields["kind"]
        else:
            self.fields["asset"].queryset = assets.exclude(status__in=["RETIRED", "DISPOSED"]).order_by("asset_tag")


class CreateWorkOrderForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(max_length=200, required=False, label="Work order title (blank = from request)")
    priority = forms.ChoiceField(choices=[("", "(from severity)"), ("LOW", "Low"), ("MEDIUM", "Medium"),
                                          ("HIGH", "High"), ("URGENT", "Urgent")], required=False)


class DowntimeForm(BootstrapFormMixin, forms.Form):
    started_at = dt_field("Downtime started", required=True)
    ended_at = dt_field("Downtime ended (blank = still down)")


class EvidenceForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField()
    description = forms.CharField(max_length=200, required=False)

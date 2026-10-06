from django import forms

from apps.assets.models import Asset
from apps.ui.forms import BootstrapFormMixin

from .models import CoverageAgreement, ServiceProvider
from .services import WORK_TYPES


class _AssetChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.asset_tag} · {obj.name} ({obj.site.code})"


class _DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kw):
        super().__init__(format="%Y-%m-%d", **kw)


class ProviderForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=150)
    contact_name = forms.CharField(max_length=120, required=False)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=40, required=False)
    notes = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}))


class AgreementForm(BootstrapFormMixin, forms.Form):
    kind = forms.ChoiceField(choices=CoverageAgreement.Kind.choices, label="Type")
    reference = forms.CharField(max_length=80, label="Contract / warranty number")
    title = forms.CharField(max_length=200)
    provider = forms.ModelChoiceField(queryset=ServiceProvider.objects.none())
    site = forms.ModelChoiceField(queryset=None, label="Site")
    start_date = forms.DateField(widget=_DateInput())
    end_date = forms.DateField(widget=_DateInput())
    assets = _AssetChoices(queryset=Asset.objects.none(), label="Covered assets (all at the chosen site)",
                           widget=forms.SelectMultiple(attrs={"size": 8}))
    excluded_work_types = forms.MultipleChoiceField(
        required=False, choices=[(w, w.title()) for w in WORK_TYPES], widget=forms.CheckboxSelectMultiple,
        label="Work types NOT covered")
    terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Coverage terms")
    exclusion_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                      label="Other exclusions (text)")
    sla_terms = forms.CharField(max_length=300, required=False, label="Provider response terms")
    sla_profile = forms.ModelChoiceField(
        queryset=None, required=False, empty_label="(none - use the site / organization SLA)",
        label="Internal SLA profile", help_text="Applied by M11 to requests / work orders on the covered assets.")
    renewal_alert_days = forms.IntegerField(min_value=0, max_value=365, initial=30, label="Renewal alert (days before end)")

    def __init__(self, *args, providers, sites, assets, sla_profiles, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sla_profile"].queryset = sla_profiles
        self.fields["provider"].queryset = providers
        self.fields["site"].queryset = sites
        self.fields["assets"].queryset = assets
        if editing:  # type, site and assets are fixed once created (assets have their own add / remove buttons)
            for f in ("kind", "site", "assets"):
                del self.fields[f]
        self.fields["excluded_work_types"].widget.attrs.pop("class", None)


class RenewForm(BootstrapFormMixin, forms.Form):
    reference = forms.CharField(max_length=80, label="New contract / warranty number")
    new_end_date = forms.DateField(widget=_DateInput(), label="New end date")


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=300)


class AddAssetForm(BootstrapFormMixin, forms.Form):
    asset = forms.ModelChoiceField(queryset=Asset.objects.none())

    def __init__(self, *args, assets, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset"].queryset = assets

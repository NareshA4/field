from __future__ import annotations

from django import forms

from apps.assets.models import Asset
from apps.incidents.models import ServiceRequest
from apps.ui.forms import BootstrapFormMixin


class _PortalAssetChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        site_name = obj.site.name or obj.site.code
        return f"{obj.asset_tag} — {obj.name} ({site_name})"


class PortalServiceRequestForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(
        max_length=200,
        min_length=3,
        label="Request Title / Subject",
        help_text="Brief summary of the issue or requested maintenance (minimum 3 characters).",
        widget=forms.TextInput(attrs={"placeholder": "e.g., Water pump pressure drop on Line 2"}),
    )
    asset = _PortalAssetChoiceField(
        queryset=Asset.objects.none(),
        label="Related Asset",
        help_text="Select the facility asset requiring service.",
    )
    description = forms.CharField(
        required=False,
        label="Problem Description",
        help_text="Detailed description of the fault symptoms, observed behavior, or specific service needed.",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Describe what happened, error codes, unusual sounds, leaks, etc."}),
    )
    severity = forms.ChoiceField(
        choices=[
            (ServiceRequest.Severity.LOW, "Low — Minor issue, system operational"),
            (ServiceRequest.Severity.MEDIUM, "Medium — Standard maintenance request"),
            (ServiceRequest.Severity.HIGH, "High — Urgent breakdown impacting operations"),
        ],
        initial=ServiceRequest.Severity.MEDIUM,
        label="Priority / Urgency",
        help_text="Select requested urgency. Note: Staff will verify and triage severity.",
    )
    service_impact = forms.ChoiceField(
        choices=ServiceRequest.Impact.choices,
        initial=ServiceRequest.Impact.NONE,
        label="Service Impact",
        help_text="Current impact on operational capability.",
    )
    impact_notes = forms.CharField(
        max_length=500,
        required=False,
        label="Impact Notes",
        help_text="Any additional context regarding operational downtime or affected areas.",
        widget=forms.TextInput(attrs={"placeholder": "e.g., Production slowed by approximately 30%"}),
    )
    attachment = forms.FileField(
        required=False,
        label="Photo / File Attachment",
        help_text="Optional document, log, or photo of the issue (up to 10 MB).",
    )

    def __init__(self, *args, organization=None, membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        if membership:
            from . import selectors
            self.fields["asset"].queryset = selectors.granted_assets_for(membership)
        elif organization:
            self.fields["asset"].queryset = (
                Asset.objects.for_organization(organization)
                .exclude(status__in=["RETIRED", "DISPOSED"])
                .select_related("site")
                .order_by("asset_tag")
            )


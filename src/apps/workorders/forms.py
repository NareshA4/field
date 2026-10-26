from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.assets.models import Asset, AssetComponent
from apps.incidents.models import ServiceRequest
from apps.incidents.workflow import APPROVED
from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy.models import Membership
from apps.ui.forms import BootstrapFormMixin

from .assignment_rules import available_technicians
from .models import WorkOrder


class DataAttributeSelect(forms.Select):
    """Select widget that exposes safe foreign-key metadata for form ergonomics only."""
    option_data = {}

    def __init__(self, *args, option_data=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.option_data = option_data or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        data = self.option_data.get(str(value), {})
        option["attrs"].update(data)
        return option


class CreateWorkOrderForm(BootstrapFormMixin, forms.Form):
    source_choices = WorkOrder.SourceType.distinct_choices()
    source_type = forms.ChoiceField(choices=[("", "Select a source") , *source_choices], initial="",
        error_messages={"required": "Choose a work-order source.",
                        "invalid_choice": "Choose a valid work-order source."})
    source_request = forms.ModelChoiceField(queryset=ServiceRequest.objects.none(), required=False, initial=None,
        label="Approved incident or service request", empty_label="Select an approved source record",
        error_messages={"invalid_choice": "Select an approved source record available to your organization."})
    source_reference = forms.CharField(max_length=150, required=False,
        label="Preventive-maintenance reference", help_text="Enter the originating PM schedule or generated job reference.")
    site = forms.ModelChoiceField(queryset=Site.objects.none(), initial=None, empty_label="Select a site", error_messages={
        "required": "Site is required.",
        "invalid_choice": "Choose an active site you are permitted to manage.",
    })
    asset = forms.ModelChoiceField(queryset=Asset.objects.none(), initial=None, label="Asset",
        empty_label="Select a site first", error_messages={
        "required": "Asset is required.",
        "invalid_choice": "Invalid asset for the selected site.",
    })
    title = forms.CharField(min_length=3, max_length=200, error_messages={
        "required": "Enter a work-order title.",
        "min_length": "Enter a clear title with at least 3 characters.",
        "max_length": "The title must be 200 characters or fewer.",
    })
    description = forms.CharField(required=False, max_length=5000,
        error_messages={"max_length": "Description must be 5,000 characters or fewer."},
        widget=forms.Textarea(attrs={"rows": 3}))
    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = membership.organization
        sites = Site.objects.for_organization(membership.organization).filter(status=Site.Status.ACTIVE)
        allowed = [s.pk for s in sites
                   if rbac.has_permission(membership, "work_order.create", s)]
        sites = sites.filter(pk__in=allowed)
        self.fields["site"].queryset = sites.order_by("name")
        site_widget_attrs = self.fields["site"].widget.attrs.copy()
        self.fields["site"].widget = DataAttributeSelect(attrs=site_widget_attrs,
            choices=self.fields["site"].choices, option_data={
            str(site.pk): {"data-site-id": str(site.pk)} for site in sites})
        site_ids = list(sites.values_list("pk", flat=True))
        linked_references = WorkOrder.objects.for_organization(membership.organization).exclude(
            source_reference=""
        ).values_list("source_reference", flat=True)
        linked_source_ids = WorkOrder.objects.for_organization(membership.organization).exclude(
            source_id__isnull=True
        ).values_list("source_id", flat=True)

        source_qs = ServiceRequest.objects.for_organization(
            membership.organization).filter(
            status=APPROVED, site_id__in=site_ids, work_order__isnull=True, duplicate_of__isnull=True
        ).exclude(
            number__in=linked_references
        ).exclude(
            pk__in=linked_source_ids
        ).select_related("site", "asset").order_by("-created_at")

        self.fields["source_request"].queryset = source_qs
        self.fields["source_request"].label_from_instance = lambda sr: f"{sr.number} · {sr.title}"
        source_widget_attrs = self.fields["source_request"].widget.attrs.copy()
        self.fields["source_request"].widget = DataAttributeSelect(attrs=source_widget_attrs,
            choices=self.fields["source_request"].choices, option_data={
            str(sr.pk): {
                "data-source-kind": ("REQUEST" if sr.kind == ServiceRequest.Kind.SERVICE_REQUEST else "INCIDENT"),
                "data-site-id": str(sr.site_id),
                "data-asset-id": str(sr.asset_id),
                "data-severity": getattr(sr, "severity", ""),
            } for sr in source_qs})

        site_value = self.data.get("site") if self.is_bound else self.initial.get("site")
        try:
            site_id = sites.get(pk=site_value).pk if site_value else None
        except (Site.DoesNotExist, ValidationError, ValueError, TypeError):
            site_id = None
        assets = Asset.objects.for_organization(membership.organization).filter(
            status=Asset.Status.ACTIVE, site__in=sites,
            **({"site_id": site_id} if site_id else {}),
        ).select_related("site").order_by("asset_tag")
        self.fields["asset"].queryset = assets
        asset_widget_attrs = self.fields["asset"].widget.attrs.copy()
        self.fields["asset"].widget = DataAttributeSelect(attrs=asset_widget_attrs,
            choices=self.fields["asset"].choices, option_data={
            str(asset.pk): {"data-site-id": str(asset.site_id)} for asset in assets})
        self.fields["asset"].label_from_instance = lambda asset: (
            f"{asset.asset_tag} · {asset.name} · {asset.site.code}")

    def clean(self):
        cleaned = super().clean()
        if "status" in self.data:
            self.add_error(None, "Status is assigned by the workflow; new work orders are created as Draft.")
        source_type, source_request = cleaned.get("source_type"), cleaned.get("source_request")
        site, asset = cleaned.get("site"), cleaned.get("asset")
        if source_type in (WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT):
            if source_request is None:
                self.add_error("source_request", "Select a valid approved incident or service request.")
            else:
                expected_kind = (ServiceRequest.Kind.SERVICE_REQUEST
                    if source_type == WorkOrder.SourceType.REQUEST else ServiceRequest.Kind.INCIDENT)
                if source_request.kind != expected_kind:
                    self.add_error("source_request", "The selected record does not match the chosen source type.")
                if site and source_request.site_id != site.pk:
                    self.add_error("site", "Site must match the selected source record.")
                if asset and source_request.asset_id != asset.pk:
                    self.add_error("asset", "Asset must match the selected source record.")

        elif source_type == WorkOrder.SourceType.MANUAL and source_request:
            self.add_error("source_request", "Manual work orders cannot be linked to an incident or request.")
        elif source_type in (WorkOrder.SourceType.PM, WorkOrder.SourceType.PREVENTIVE_MAINTENANCE):
            if not cleaned.get("source_reference", "").strip():
                self.add_error("source_reference", "Enter the originating preventive-maintenance schedule reference.")
        elif source_type == WorkOrder.SourceType.MANUAL and cleaned.get("source_reference", "").strip():
            self.add_error("source_reference", "Manual work orders cannot contain a source reference.")
        if site and asset and asset.site_id != site.pk:
            self.add_error("asset", "Invalid asset for the selected site.")
        if self.is_bound and not asset and self.data.get("asset"):
            candidate = Asset.objects.for_organization(self.organization).filter(pk=self.data.get("asset")).first()
            if candidate:
                if candidate.status != Asset.Status.ACTIVE:
                    self.add_error("asset", "Selected asset is inactive.")
                elif site and candidate.site_id != site.pk:
                    self.add_error("asset", "Invalid asset for the selected site.")

        title = cleaned.get("title")
        if title and len(title.strip()) < 3:
            self.add_error("title", "Enter a clear title with at least 3 non-space characters.")
        return cleaned

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title or any(not (character.isalnum() or character.isspace() or character in "-_") for character in title):
            raise ValidationError("Title can contain letters, numbers, hyphens, underscores, and spaces only.")
        return title

class EditWorkOrderForm(BootstrapFormMixin, forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.none())
    asset = forms.ModelChoiceField(queryset=Asset.objects.none(), label="Asset")
    component = forms.ModelChoiceField(queryset=AssetComponent.objects.none(), required=False, label="Component",
                                       empty_label="No component selected")
    title = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    work_type = forms.ChoiceField(choices=[("", "Not set"), *WorkOrder.WorkType.choices], required=False)
    priority = forms.ChoiceField(choices=[("", "Not set"), *WorkOrder.Priority.choices], required=False)
    planned_start = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    planned_end = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                      widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    due_at = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                 widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    estimated_hours = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0.01, required=False)

    def __init__(self, *args, membership, work_order, **kwargs):
        initial = {
            "site": work_order.site_id, "title": work_order.title, "description": work_order.description,
            "asset": work_order.asset_id,
            "component": (AssetComponent.objects.filter(child_id=work_order.component_id).values_list("pk", flat=True).first()
                          if work_order.component_id else None),
            "work_type": work_order.work_type,
            "priority": work_order.priority, "planned_start": work_order.planned_start,
            "planned_end": work_order.planned_end, "due_at": work_order.due_at,
            "estimated_hours": work_order.estimated_hours,
        }
        initial.update(kwargs.pop("initial", {}))
        super().__init__(*args, initial=initial, **kwargs)
        if work_order.source_request_id:
            self.fields["priority"].disabled = True
            self.fields["priority"].help_text = "Inherited from the linked approved source record."
        if work_order.status == WorkOrder.Status.DRAFT and work_order.source_request_id:
            self.fields["site"].disabled = True
            self.fields["asset"].disabled = True
            self.fields["component"].disabled = True
        self.fields["site"].queryset = Site.objects.for_organization(membership.organization).filter(
            status=Site.Status.ACTIVE)
        allowed = [s.pk for s in self.fields["site"].queryset
                   if rbac.has_permission(membership, "work_order.update", s)]
        self.fields["site"].queryset = self.fields["site"].queryset.filter(pk__in=allowed)
        selected_site = self.data.get("site") if self.is_bound else self.initial.get("site")
        assets = Asset.objects.for_organization(membership.organization).filter(
            status=Asset.Status.ACTIVE, site__in=self.fields["site"].queryset)
        if selected_site:
            assets = assets.filter(site_id=selected_site)
        self.fields["asset"].queryset = assets.select_related("site").order_by("asset_tag")
        self.fields["asset"].label_from_instance = lambda asset: f"{asset.asset_tag} · {asset.name} · {asset.site.code}"
        self.fields["component"].queryset = AssetComponent.objects.filter(
            organization=membership.organization, parent__in=assets, child__status=Asset.Status.ACTIVE,
            relationship_type=AssetComponent.Relationship.COMPONENT).select_related("parent", "child")
        if work_order.status == WorkOrder.Status.PLANNED:
            planning_fields = {"work_type", "priority", "planned_start", "planned_end", "due_at", "estimated_hours"}
            for name in list(self.fields):
                if name not in planning_fields:
                    self.fields.pop(name)
        if "component" in self.fields:
            self.fields["component"].label_from_instance = lambda link: f"{link.parent.asset_tag} → {link.child.asset_tag} · {link.child.name}"
    def clean(self):
        cleaned = super().clean()
        site, asset, component = cleaned.get("site"), cleaned.get("asset"), cleaned.get("component")
        if site and asset and asset.site_id != site.pk:
            self.add_error("asset", "Invalid asset for the selected site.")
        if component and asset and component.parent_id != asset.pk:
            self.add_error("component", "Invalid component for the selected asset.")
        start, end, due = cleaned.get("planned_start"), cleaned.get("planned_end"), cleaned.get("due_at")
        if start and end and end <= start:
            self.add_error("planned_end", "Planned end must be after planned start.")
        if due and end and due < end:
            self.add_error("due_at", "Due date must be on or after planned end.")
        if due and start and due <= start:
            self.add_error("due_at", "Due date must be later than planned start.")
        hours = cleaned.get("estimated_hours")
        if hours is not None and hours <= 0:
            self.add_error("estimated_hours", "Estimated hours must be greater than zero.")
        elif start and end and end > start and hours is not None:
            available_hours = Decimal(str((end - start).total_seconds())) / Decimal("3600")
            if hours > available_hours:
                self.add_error("estimated_hours", "Estimated hours cannot exceed the planned start-to-end window.")
        return cleaned


class PlanForm(BootstrapFormMixin, forms.Form):
    work_type = forms.ChoiceField(choices=[("", "Choose a work type"), *WorkOrder.WorkType.choices], initial="",
        error_messages={"required": "Choose a work type before planning this work order."})
    priority = forms.ChoiceField(choices=[("", "Choose a priority"), *WorkOrder.Priority.choices], initial="",
        error_messages={"required": "Choose a priority before planning this work order."})
    planned_start = forms.DateTimeField(label="Planned start", input_formats=["%Y-%m-%dT%H:%M"],
        error_messages={"required": "Enter the planned start date and time.",
                        "invalid": "Enter a valid planned start date and time."},
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    planned_end = forms.DateTimeField(label="Planned end", input_formats=["%Y-%m-%dT%H:%M"],
        error_messages={"required": "Enter the planned end date and time.",
                        "invalid": "Enter a valid planned end date and time."},
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    due_at = forms.DateTimeField(label="Due at", input_formats=["%Y-%m-%dT%H:%M"],
        error_messages={"required": "Enter the due date and time before planning this work order.",
                        "invalid": "Enter a valid due date and time."},
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    estimated_hours = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0.01,
        error_messages={"required": "Enter estimated hours.", "invalid": "Enter estimated hours as a number.",
                        "min_value": "Estimated hours must be greater than zero."},
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "placeholder": "e.g. 2.00"}),
        help_text="Required. Must be greater than zero and fit within the planned start-to-end duration.")

    def __init__(self, *args, work_order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if work_order:
            default_work_type = work_order.work_type
            if not default_work_type:
                if work_order.source_type == WorkOrder.SourceType.INCIDENT or (
                    work_order.source_request and work_order.source_request.kind == "INCIDENT"
                ):
                    default_work_type = WorkOrder.WorkType.CORRECTIVE
                elif work_order.source_type in (WorkOrder.SourceType.PM, WorkOrder.SourceType.PREVENTIVE_MAINTENANCE):
                    default_work_type = WorkOrder.WorkType.PREVENTIVE
                elif work_order.source_request and work_order.source_request.kind == "SERVICE_REQUEST":
                    default_work_type = WorkOrder.WorkType.CORRECTIVE

            if default_work_type:
                self.initial.setdefault("work_type", default_work_type)

            default_priority = work_order.priority
            if not default_priority and work_order.source_request:
                default_priority = {
                    "LOW": WorkOrder.Priority.LOW,
                    "MEDIUM": WorkOrder.Priority.MEDIUM,
                    "HIGH": WorkOrder.Priority.HIGH,
                    "CRITICAL": WorkOrder.Priority.CRITICAL,
                }.get(getattr(work_order.source_request, "severity", ""), "")

            if default_priority:
                self.initial.setdefault("priority", default_priority)

            if work_order.source_request_id:
                self.fields["priority"].help_text = "Defaulted from linked source record; can be adjusted manually." 

    def clean(self):
        cleaned = super().clean()
        start, end, due = (cleaned.get("planned_start"), cleaned.get("planned_end"), cleaned.get("due_at"))
        if start and end and end <= start:
            self.add_error("planned_end", "Planned end must be later than planned start.")
        if due and end and due < end:
            self.add_error("due_at", "Due date must be on or after planned end.")
        if due and start and due <= start:
            self.add_error("due_at", "Due date must be later than planned start.")
        hours = cleaned.get("estimated_hours")
        if hours is not None and hours <= 0:
            self.add_error("estimated_hours", "Estimated hours must be greater than zero.")
        elif start and end and end > start and hours is not None:
            available_hours = Decimal(str((end - start).total_seconds())) / Decimal("3600")
            if hours > available_hours:
                self.add_error("estimated_hours", "Estimated hours cannot exceed the planned start-to-end window.")
        return cleaned


class AssignForm(BootstrapFormMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=Membership.objects.none(), label="Technician",
                                         empty_label="Select an available technician")
    scheduled_start = forms.DateTimeField(label="Scheduled start", input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    scheduled_end = forms.DateTimeField(label="Scheduled end", input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, membership, site, work_order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if work_order is not None:
            self.initial.setdefault("scheduled_start", work_order.planned_start)
            self.initial.setdefault("scheduled_end", work_order.planned_end)
        self.fields["assigned_to"].queryset = (available_technicians(work_order)
            if work_order is not None else Membership.objects.none())
        self.fields["assigned_to"].label_from_instance = lambda member: (
            f"{member.user.full_name} · {member.user.email}")
        self.fields["assigned_to"].help_text = ""

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("scheduled_start"), cleaned.get("scheduled_end")
        if start and end and end <= start:
            self.add_error("scheduled_end", "Scheduled end must be later than scheduled start.")
        return cleaned


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=500)


class WorkUpdateForm(BootstrapFormMixin, forms.Form):
    update = forms.CharField(min_length=5, max_length=1000, label="Work update",
                             help_text="Describe progress, findings, blockers, or the next step.",
                             widget=forms.Textarea(attrs={"rows": 4}))


class CompleteForm(BootstrapFormMixin, forms.Form):
    resolution_notes = forms.CharField(
        min_length=10,
        label="Resolution notes",
        help_text="Summarize the repair and outcome. Saved when the work order is completed.",
        error_messages={
            "required": "Enter the work performed and its outcome.",
            "min_length": "Enter at least 10 characters describing the work and outcome.",
        },
        widget=forms.Textarea(attrs={"rows": 4}),
    )

class ChecklistForm(BootstrapFormMixin, forms.Form):
    items = forms.CharField(label="Provisional checklist notes", help_text="Not used to validate completion.",
                            widget=forms.Textarea(attrs={"rows": 5}))

    def clean_items(self):
        labels = [line.strip() for line in self.cleaned_data["items"].splitlines() if line.strip()]
        if not labels:
            raise forms.ValidationError("Add at least one checklist item.")
        return [{"label": label} for label in labels]


class ReviewForm(BootstrapFormMixin, forms.Form):
    approved = forms.BooleanField(required=False, label="Approve and close")
    notes = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ClosureCommentForm(BootstrapFormMixin, forms.Form):
    comment = forms.CharField(min_length=1, max_length=1000,
                              widget=forms.Textarea(attrs={"rows": 3}),
                              help_text="This decision is appended to the work-order approval history.")


class LaborForm(BootstrapFormMixin, forms.Form):
    work_date = forms.DateField(label="Work date", widget=forms.DateInput(attrs={"type": "date"}),
                                help_text="Use today or an earlier date.")
    hours = forms.DecimalField(label="Hours worked", max_digits=4, decimal_places=2,
        min_value=Decimal("0.01"), max_value=Decimal("24"),
        help_text="Enter more than 0 and no more than 24 hours for this entry.")
    description = forms.CharField(label="Work performed", min_length=5, max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}), help_text="Summarize the labor performed.")

    def clean_work_date(self):
        work_date = self.cleaned_data["work_date"]
        if work_date > timezone.localdate():
            raise forms.ValidationError("Work date cannot be in the future.")
        return work_date


class MaterialForm(BootstrapFormMixin, forms.Form):
    item_name = forms.CharField(label="Part or material", min_length=2, max_length=150)
    part_reference = forms.CharField(label="Part reference", max_length=100, required=False)
    quantity = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
                                  label="Quantity used")
    unit = forms.CharField(label="Unit", max_length=30, required=False,
                           help_text="For example: each, metre, litre.")
    notes = forms.CharField(label="Usage notes", max_length=500, required=False,
                             widget=forms.Textarea(attrs={"rows": 3}))


class EvidenceForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField()
    description = forms.CharField(max_length=200, required=False)

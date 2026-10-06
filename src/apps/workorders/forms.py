from django import forms

from apps.rbac import services as rbac
from apps.sites.models import Site
from apps.tenancy.models import Membership
from apps.ui.forms import BootstrapFormMixin

from .models import WorkOrder


class CreateWorkOrderForm(BootstrapFormMixin, forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.none())
    title = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    source_type = forms.ChoiceField(choices=WorkOrder.SourceType.choices, initial=WorkOrder.SourceType.MANUAL)
    source_reference = forms.CharField(max_length=150, required=False, label="Source reference")
    asset_reference = forms.CharField(max_length=100, required=False, label="Asset reference")
    work_type = forms.ChoiceField(choices=WorkOrder.WorkType.choices)
    priority = forms.ChoiceField(choices=WorkOrder.Priority.choices)
    planned_start = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    planned_end = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                      widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    due_at = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                 widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    estimated_hours = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0, required=False)
    checklist = forms.CharField(required=False, label="Required checklist items (one per line)",
                                widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site"].queryset = Site.objects.for_organization(membership.organization).filter(
            status=Site.Status.ACTIVE)
        allowed = [s.pk for s in self.fields["site"].queryset
                   if rbac.has_permission(membership, "work_order.create", s)]
        self.fields["site"].queryset = self.fields["site"].queryset.filter(pk__in=allowed)

    def clean(self):
        cleaned = super().clean()
        start, end, due = cleaned.get("planned_start"), cleaned.get("planned_end"), cleaned.get("due_at")
        if start and end and end <= start:
            self.add_error("planned_end", "Planned end must be after planned start.")
        if due and start and due < start:
            self.add_error("due_at", "Due date cannot be before planned start.")
        if cleaned.get("source_type") != WorkOrder.SourceType.MANUAL and not cleaned.get("source_reference", "").strip():
            self.add_error("source_reference", "A source reference is required.")
        return cleaned

    def clean_checklist(self):
        return [{"label": line.strip(), "required": True, "completed": False}
                for line in self.cleaned_data["checklist"].splitlines() if line.strip()]


class EditWorkOrderForm(BootstrapFormMixin, forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.none())
    title = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    source_type = forms.ChoiceField(choices=WorkOrder.SourceType.choices)
    source_reference = forms.CharField(max_length=150, required=False, label="Source reference")
    asset_reference = forms.CharField(max_length=100, required=False, label="Asset reference")
    work_type = forms.ChoiceField(choices=WorkOrder.WorkType.choices)
    priority = forms.ChoiceField(choices=WorkOrder.Priority.choices)
    planned_start = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    planned_end = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                      widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    due_at = forms.DateTimeField(required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                 widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    estimated_hours = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0)

    def __init__(self, *args, membership, work_order, **kwargs):
        initial = {
            "site": work_order.site_id, "title": work_order.title, "description": work_order.description,
            "source_type": work_order.source_type, "source_reference": work_order.source_reference,
            "asset_reference": work_order.asset_reference, "work_type": work_order.work_type,
            "priority": work_order.priority, "planned_start": work_order.planned_start,
            "planned_end": work_order.planned_end, "due_at": work_order.due_at,
            "estimated_hours": work_order.estimated_hours,
        }
        initial.update(kwargs.pop("initial", {}))
        super().__init__(*args, initial=initial, **kwargs)
        self.fields["site"].queryset = Site.objects.for_organization(membership.organization).filter(
            status=Site.Status.ACTIVE)
        allowed = [s.pk for s in self.fields["site"].queryset
                   if rbac.has_permission(membership, "work_order.update", s)]
        self.fields["site"].queryset = self.fields["site"].queryset.filter(pk__in=allowed)
        if work_order.status != WorkOrder.Status.DRAFT:
            self.fields["site"].disabled = True

    def clean(self):
        cleaned = super().clean()
        start, end, due = cleaned.get("planned_start"), cleaned.get("planned_end"), cleaned.get("due_at")
        if start and end and end <= start:
            self.add_error("planned_end", "Planned end must be after planned start.")
        if due and start and due < start:
            self.add_error("due_at", "Due date cannot be before planned start.")
        if cleaned.get("source_type") != WorkOrder.SourceType.MANUAL and not cleaned.get("source_reference", "").strip():
            self.add_error("source_reference", "A source reference is required.")
        return cleaned


class PlanForm(BootstrapFormMixin, forms.Form):
    planned_start = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    planned_end = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    estimated_hours = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0, required=False)


class AssignForm(BootstrapFormMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=Membership.objects.none(), label="Technician")
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, membership, site, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Membership.objects.for_organization(membership.organization).filter(
            status=Membership.Status.ACTIVE).select_related("user")
        allowed = [m.pk for m in qs if rbac.has_permission(m, "work_order.start", site)]
        self.fields["assigned_to"].queryset = qs.filter(pk__in=allowed)


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=500)


class WorkUpdateForm(BootstrapFormMixin, forms.Form):
    update = forms.CharField(min_length=5, max_length=1000, label="Work update",
                             help_text="Describe progress, findings, blockers, or the next step.",
                             widget=forms.Textarea(attrs={"rows": 4}))


class CompleteForm(BootstrapFormMixin, forms.Form):
    resolution_notes = forms.CharField(min_length=10, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, work_order, **kwargs):
        super().__init__(*args, **kwargs)
        self.checklist_items = [item for item in work_order.checklist_items if isinstance(item, dict)]
        for index, item in enumerate(self.checklist_items):
            self.fields[f"checklist_item_{index}"] = forms.BooleanField(
                required=False,
                initial=bool(item.get("completed")),
                label=item.get("label", f"Checklist item {index + 1}"),
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )

    def clean(self):
        cleaned = super().clean()
        cleaned["checklist_items"] = [
            {**item, "completed": bool(cleaned.get(f"checklist_item_{index}", False))}
            for index, item in enumerate(self.checklist_items)
        ]
        return cleaned


class ChecklistForm(BootstrapFormMixin, forms.Form):
    items = forms.CharField(help_text='One required checklist item per line.',
                            widget=forms.Textarea(attrs={"rows": 5}))

    def clean_items(self):
        labels = [line.strip() for line in self.cleaned_data["items"].splitlines() if line.strip()]
        if not labels:
            raise forms.ValidationError("Add at least one checklist item.")
        return [{"label": label, "required": True, "completed": False} for label in labels]


class ReviewForm(BootstrapFormMixin, forms.Form):
    approved = forms.BooleanField(required=False, label="Approve and close")
    notes = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ClosureCommentForm(BootstrapFormMixin, forms.Form):
    comment = forms.CharField(min_length=1, max_length=1000,
                              widget=forms.Textarea(attrs={"rows": 3}),
                              help_text="This decision is appended to the work-order approval history.")


class LaborForm(BootstrapFormMixin, forms.Form):
    work_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    hours = forms.DecimalField(max_digits=4, decimal_places=2, min_value=0)
    description = forms.CharField(max_length=500)


class MaterialForm(BootstrapFormMixin, forms.Form):
    item_name = forms.CharField(max_length=150)
    part_reference = forms.CharField(max_length=100, required=False)
    quantity = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    unit = forms.CharField(max_length=30, required=False)
    notes = forms.CharField(max_length=500, required=False)


class EvidenceForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField()
    description = forms.CharField(max_length=200, required=False)

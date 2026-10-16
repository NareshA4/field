from django import forms
from django.forms import BaseFormSet, formset_factory

from apps.rbac.models import Role
from apps.assets.models import Asset, AssetCategory
from apps.contracts.models import CoverageAgreement
from apps.sites.models import Site
from apps.tenancy.models import Membership
from apps.ui.forms import BootstrapFormMixin

from .models import EscalationRule, SLAProfile
from .services import REQUEST_PRIORITIES, WORK_ORDER_PRIORITIES, priorities_for, valid_pause_tokens


class _SiteChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} · {obj.name}"


class _ClientChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.user.display_name} · {obj.user.email}"


def _pause_choices(applies_to=None):
    tokens = valid_pause_tokens("REQUEST")
    labels = []
    for token in sorted(tokens):
        kind, state = token.split(":", 1)
        subject = "Request" if kind == "REQUEST" else "Work Order"
        labels.append((token, f"{subject} · {state.replace('_', ' ').title()}"))
    return labels


class AppliesToChoiceField(forms.ChoiceField):
    def valid_value(self, value):
        if super().valid_value(value):
            return True
        s = str(value).strip().upper()
        return s in ("REQUEST", "WORK_ORDER", "INCIDENT / BREAKDOWN", "WORK ORDER", "SERVICE REQUEST", "SERVICE REQUESTS / INCIDENTS")


class ProfileForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=120, label="Name")
    description = forms.CharField(max_length=300, required=False, label="Description")
    applies_to = AppliesToChoiceField(
        choices=[
            ("REQUEST", "Incident / Breakdown"),
            ("WORK_ORDER", "Work Order"),
        ],
        label="Applies to"
    )
    site = _SiteChoice(queryset=Site.objects.none(), required=False, label="Site (blank = whole organization)")
    client = _ClientChoice(queryset=Membership.objects.none(), required=False, label="Client / requester")
    contract = forms.ModelChoiceField(queryset=CoverageAgreement.objects.none(), required=False,
                                      label="Contract / AMC")
    asset = forms.ModelChoiceField(queryset=Asset.objects.none(), required=False, label="Asset")
    asset_category = forms.ModelChoiceField(queryset=AssetCategory.objects.none(), required=False,
                                            label="Asset category")
    service_type = forms.ChoiceField(choices=[], required=False, label="Service type")
    work_type = forms.CharField(max_length=20, required=False,
                                label="Work type (work-order profiles, optional)")
    coverage_only = forms.BooleanField(
        required=False, label="Coverage-only profile",
        help_text="Apply only to assets covered by a warranty / AMC / contract that names this profile; it is "
                  "never picked by site or organization.")
    is_active = forms.BooleanField(required=False, initial=True, label="Activate this profile when saved")
    pause_states = forms.MultipleChoiceField(
        required=False, choices=_pause_choices(), widget=forms.CheckboxSelectMultiple,
        label="Pause the timers while the item is in",
        help_text="Leave empty to never pause (timers keep running).")

    def __init__(self, *args, sites=None, clients=None, contracts=None, assets=None, categories=None,
                 applies_to="REQUEST", editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            raw_applies = self.data.get("applies_to") or applies_to
            applies_to = "WORK_ORDER" if str(raw_applies).strip().upper() in ("WORK_ORDER", "WORK ORDER") or "WORK" in str(raw_applies).upper() else "REQUEST"
        self.fields["pause_states"].choices = _pause_choices(applies_to)
        self.fields["site"].queryset = sites if sites is not None else Site.objects.none()
        self.fields["client"].queryset = clients or Membership.objects.none()
        self.fields["client"].queryset = self.fields["client"].queryset.select_related("user")
        self.fields["contract"].queryset = contracts or CoverageAgreement.objects.none()
        self.fields["asset"].queryset = assets or Asset.objects.none()
        self.fields["asset_category"].queryset = categories or AssetCategory.objects.none()
        from apps.incidents.models import ServiceRequest
        from apps.workorders.models import WorkOrder
        self.fields["service_type"].choices = [("", "Any service type")] + (
            list(ServiceRequest.Kind.choices) if applies_to == "REQUEST" else list(WorkOrder.WorkType.choices))
        self.fields["service_type"].help_text = (
            "Incident / Breakdown or Service Request" if applies_to == "REQUEST" else
            "Work Order type, for example Corrective or Emergency")
        if editing:
            self.fields.pop("is_active")

    def clean_applies_to(self):
        val = self.cleaned_data.get("applies_to")
        if not val:
            return "REQUEST"
        s = str(val).strip()
        if s in ("REQUEST", "WORK_ORDER"):
            return s
        if "WORK" in s.upper():
            return "WORK_ORDER"
        return "REQUEST"


class TargetForm(BootstrapFormMixin, forms.Form):
    priority = forms.CharField(required=False, label="Priority")
    response_minutes = forms.CharField(
        required=False, label="Response Target",
        widget=forms.TextInput(attrs={"placeholder": "e.g. 15 minutes"}))
    resolution_minutes = forms.CharField(
        required=False, label="Resolution Target",
        widget=forms.TextInput(attrs={"placeholder": "e.g. 4 hours"}))
    warning_percent = forms.CharField(
        required=False, initial=80, label="Warning Alert",
        widget=forms.TextInput(attrs={"placeholder": "e.g. 75%"}))

    def __init__(self, *args, applies_to="REQUEST", **kwargs):
        super().__init__(*args, **kwargs)
        self.applies_to = "WORK_ORDER" if str(applies_to).strip().upper() in ("WORK_ORDER", "WORK ORDER") or "WORK" in str(applies_to).upper() else "REQUEST"
        if self.applies_to == "WORK_ORDER":
            choices = [("", "Choose priority (optional)"), ("URGENT", "Urgent"), ("HIGH", "High"), ("MEDIUM", "Medium"), ("LOW", "Low")]
        else:
            choices = [("", "Choose priority (optional)"), ("CRITICAL", "Critical"), ("HIGH", "High"), ("MEDIUM", "Medium"), ("LOW", "Low")]
        self.fields["priority"] = forms.CharField(
            required=False, label="Priority",
            widget=forms.Select(choices=choices)
        )

    def clean(self):
        cleaned = super().clean()
        pfx = f"{self.prefix}-" if self.prefix else ""

        priority_val = cleaned.get("priority")
        if not priority_val and self.is_bound:
            priority_val = self.data.get(f"{pfx}priority")

        resp_val = cleaned.get("response_minutes")
        if (resp_val is None or str(resp_val).strip() == "") and self.is_bound:
            resp_val = self.data.get(f"{pfx}response_target") or self.data.get(f"{pfx}response_minutes") or self.data.get(f"{pfx}response")

        resol_val = cleaned.get("resolution_minutes")
        if (resol_val is None or str(resol_val).strip() == "") and self.is_bound:
            resol_val = self.data.get(f"{pfx}resolution_target") or self.data.get(f"{pfx}resolution_minutes") or self.data.get(f"{pfx}resolution")

        warn_val = cleaned.get("warning_percent")
        if (warn_val is None or str(warn_val).strip() == "") and self.is_bound:
            warn_val = self.data.get(f"{pfx}warning_alert") or self.data.get(f"{pfx}warning_percent") or self.data.get(f"{pfx}warn_at")

        # Completely empty row check
        if not priority_val and (resp_val is None or str(resp_val).strip() == "") and (resol_val is None or str(resol_val).strip() == ""):
            return {}

        from .services import parse_duration_minutes, parse_percentage
        valid_prios = WORK_ORDER_PRIORITIES if self.applies_to == "WORK_ORDER" else REQUEST_PRIORITIES

        # Validate priority
        if not priority_val:
            self.add_error("priority", "Choose a priority.")
        else:
            clean_p = str(priority_val).strip().upper()
            if clean_p not in valid_prios:
                self.add_error("priority", f"Priority must be one of {', '.join(p.title() for p in valid_prios)}.")
            else:
                cleaned["priority"] = clean_p

        # Validate response target
        if resp_val is None or str(resp_val).strip() == "":
            self.add_error("response_minutes", "Response Target is required.")
        else:
            try:
                resp_mins = parse_duration_minutes(resp_val, what="Response Target")
                if resp_mins <= 0:
                    self.add_error("response_minutes", "Response Target must be greater than zero.")
                else:
                    cleaned["response_minutes"] = resp_mins
            except Exception as e:
                self.add_error("response_minutes", str(e))

        # Validate resolution target
        if resol_val is None or str(resol_val).strip() == "":
            self.add_error("resolution_minutes", "Resolution Target is required.")
        else:
            try:
                resol_mins = parse_duration_minutes(resol_val, what="Resolution Target")
                if resol_mins <= 0:
                    self.add_error("resolution_minutes", "Resolution Target must be greater than zero.")
                else:
                    cleaned["resolution_minutes"] = resol_mins
            except Exception as e:
                self.add_error("resolution_minutes", str(e))

        # Validate warning alert
        if warn_val is None or str(warn_val).strip() == "":
            cleaned["warning_percent"] = 80
        else:
            try:
                warn_pct = parse_percentage(warn_val, what="Warning Alert")
                if warn_pct < 1 or warn_pct > 99:
                    self.add_error("warning_percent", "Warning Alert must be between 1% and 99%.")
                else:
                    cleaned["warning_percent"] = warn_pct
            except Exception as e:
                self.add_error("warning_percent", str(e))

        # Check resolution >= response
        if cleaned.get("response_minutes") and cleaned.get("resolution_minutes"):
            if cleaned["resolution_minutes"] < cleaned["response_minutes"]:
                self.add_error("resolution_minutes", "The Resolution Target cannot be shorter than the Response Target.")

        return cleaned


class PriorityTargetFormSet(BaseFormSet):
    def __init__(self, *args, applies_to="REQUEST", is_draft=False, **kwargs):
        self.applies_to = "WORK_ORDER" if str(applies_to).strip().upper() in ("WORK_ORDER", "WORK ORDER") or "WORK" in str(applies_to).upper() else "REQUEST"
        self.is_draft = is_draft
        super().__init__(*args, form_kwargs={"applies_to": self.applies_to}, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        active_priorities = []
        for form in self.forms:
            if not form.cleaned_data:
                continue
            prio = form.cleaned_data.get("priority")
            if prio:
                active_priorities.append(prio)
        if not active_priorities:
            if not self.is_draft:
                raise forms.ValidationError("An active SLA Profile requires at least one complete Priority Target (both Response Target and Resolution Target). Define a target or save as Draft.")
            return
        if len(active_priorities) != len(set(active_priorities)):
            duplicates = [p for p in set(active_priorities) if active_priorities.count(p) > 1]
            dup_str = ", ".join(d.title() for d in duplicates)
            raise forms.ValidationError(f"Duplicate priority target detected: {dup_str}. Each priority level can only have one target in an SLA Profile.")


PriorityTargetFormSetFactory = formset_factory(
    TargetForm, formset=PriorityTargetFormSet, extra=0, min_num=1, validate_min=False,
    max_num=4, validate_max=True,
)


class RuleForm(BootstrapFormMixin, forms.Form):
    target_kind = forms.ChoiceField(required=False,
                                    choices=[("", "Choose target"), ("RESPONSE", "Response Target"),
                                             ("RESOLUTION", "Resolution Target")], label="Target")
    trigger = forms.ChoiceField(required=False,
                                choices=[("", "Choose trigger"), *EscalationRule.Trigger.choices], label="When")
    after_minutes = forms.IntegerField(required=False, min_value=1, label="Escalate after (minutes, Escalation only)")
    level = forms.IntegerField(min_value=1, max_value=3, initial=1, label="Escalation level")
    notify_role = forms.ModelChoiceField(queryset=Role.objects.none(), required=False, label="Notify role")
    notify_assignee = forms.BooleanField(required=False, label="Notify the assignee")

    def __init__(self, *args, roles=None, **kwargs):
        super().__init__(*args, **kwargs)
        if roles is not None:
            self.fields["notify_role"].queryset = roles

    def clean(self):
        cleaned = super().clean()
        target, trigger = cleaned.get("target_kind"), cleaned.get("trigger")
        role, assignee = cleaned.get("notify_role"), cleaned.get("notify_assignee")
        after = cleaned.get("after_minutes")
        if not any((target, trigger, role, assignee, after)):
            return cleaned
        if not target:
            self.add_error("target_kind", "Choose which target this rule monitors.")
        if not trigger:
            self.add_error("trigger", "Choose when this rule should run.")
        if not role and not assignee:
            self.add_error("notify_role", "Choose a role or the assignee to notify.")
        if trigger == EscalationRule.Trigger.ESCALATION and not after:
            self.add_error("after_minutes", "Set how many minutes after the SLA Breach to escalate.")
        if trigger and trigger != EscalationRule.Trigger.ESCALATION and after:
            self.add_error("after_minutes", "Only Escalation rules use an after-breach delay.")
        return cleaned


class EscalationRuleFormSet(BaseFormSet):
    def __init__(self, *args, roles=None, **kwargs):
        super().__init__(*args, form_kwargs={"roles": roles}, **kwargs)


EscalationRuleFormSetFactory = formset_factory(
    RuleForm, formset=EscalationRuleFormSet, extra=1, can_delete=True, max_num=6, validate_max=True,
)

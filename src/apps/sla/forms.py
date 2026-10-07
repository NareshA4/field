from django import forms

from apps.rbac.models import Role
from apps.ui.forms import BootstrapFormMixin

from .models import EscalationRule, SLAProfile
from .services import REQUEST_PRIORITIES, WORK_ORDER_PRIORITIES, valid_pause_tokens


class _SiteChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} · {obj.name}"


def _pause_choices(applies_to=None):
    tokens = valid_pause_tokens(applies_to or "REQUEST")
    return [(t, t.replace(":", " · ").replace("_", " ").title()) for t in sorted(tokens)]


class ProfileForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=120)
    description = forms.CharField(max_length=300, required=False)
    applies_to = forms.ChoiceField(choices=SLAProfile.AppliesTo.choices, label="Applies to")
    site = _SiteChoice(queryset=None, required=False, label="Site (blank = whole organization)")
    work_type = forms.CharField(max_length=20, required=False, label="Work type (work-order profiles, optional)")
    coverage_only = forms.BooleanField(
        required=False, label="Coverage-only profile",
        help_text="Apply only to assets covered by a warranty / AMC / contract that names this profile; it is "
                  "never picked by site or organization.")
    pause_states = forms.MultipleChoiceField(
        required=False, choices=_pause_choices(), widget=forms.CheckboxSelectMultiple,
        label="Pause the timers while the item is in",
        help_text="Leave empty to never pause (timers keep running).")

    def __init__(self, *args, sites=None, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pause_states"].choices = _pause_choices()
        if editing:
            for f in ("applies_to", "site", "work_type", "coverage_only"):
                del self.fields[f]
        else:
            self.fields["site"].queryset = sites


class TargetForm(BootstrapFormMixin, forms.Form):
    priority = forms.ChoiceField(choices=[])
    response_minutes = forms.IntegerField(min_value=1, label="Respond within (minutes)")
    resolution_minutes = forms.IntegerField(min_value=1, label="Resolve within (minutes)")
    warning_percent = forms.IntegerField(min_value=1, max_value=99, initial=80, label="Warn at (% of target)")

    def __init__(self, *args, applies_to="REQUEST", **kwargs):
        super().__init__(*args, **kwargs)
        pr = REQUEST_PRIORITIES if applies_to == "REQUEST" else WORK_ORDER_PRIORITIES
        self.fields["priority"].choices = [(p, p.title()) for p in pr]


class RuleForm(BootstrapFormMixin, forms.Form):
    target_kind = forms.ChoiceField(choices=[("RESPONSE", "Response"), ("RESOLUTION", "Resolution")], label="Target")
    trigger = forms.ChoiceField(choices=EscalationRule.Trigger.choices)
    after_minutes = forms.IntegerField(required=False, min_value=1, label="Escalate after (minutes, escalation only)")
    level = forms.IntegerField(min_value=1, max_value=3, initial=1)
    notify_role = forms.ModelChoiceField(queryset=Role.objects.none(), required=False, label="Notify role")
    notify_assignee = forms.BooleanField(required=False, label="Notify the assignee")

    def __init__(self, *args, roles=None, **kwargs):
        super().__init__(*args, **kwargs)
        if roles is not None:
            self.fields["notify_role"].queryset = roles

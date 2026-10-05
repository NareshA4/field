from django import forms

from apps.ui.forms import BootstrapFormMixin

from .models import MAX_ESCALATION_ORDER, Zone

WEEKDAYS = [(1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"), (5, "Friday"), (6, "Saturday"),
            (7, "Sunday")]


class SiteForm(BootstrapFormMixin, forms.Form):
    code = forms.CharField(max_length=30, help_text="Short unique identifier, e.g. PLANT-01.")
    name = forms.CharField(max_length=150)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    city = forms.CharField(max_length=100, required=False)
    state_region = forms.CharField(max_length=100, required=False, label="State / region")
    postal_code = forms.CharField(max_length=20, required=False)
    country = forms.CharField(max_length=2, required=False, help_text="ISO country code, e.g. IN")
    timezone = forms.CharField(max_length=64, help_text="IANA name, e.g. Asia/Kolkata")
    contact_name = forms.CharField(max_length=150, required=False)
    contact_email = forms.EmailField(required=False)
    contact_phone = forms.CharField(max_length=32, required=False)


class ZoneForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=120)
    zone_type = forms.ChoiceField(choices=Zone.ZoneType.choices, label="Type")
    code = forms.CharField(max_length=30, required=False)
    parent = forms.ModelChoiceField(queryset=Zone.objects.none(), required=False,
                                    empty_label="(top level of the site)")
    description = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, site, zone=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Zone.objects.filter(site=site, status="ACTIVE").order_by("name")
        if zone is not None:
            exclude = {zone.pk}
            frontier = [zone.pk]
            while frontier:  # a location cannot be moved below itself or its descendants
                frontier = list(Zone.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
                exclude.update(frontier)
            qs = qs.exclude(pk__in=exclude)
        self.fields["parent"].queryset = qs


class CalendarForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=100)
    is_24x7 = forms.BooleanField(required=False, label="Operates 24x7")
    working_days = forms.MultipleChoiceField(choices=WEEKDAYS, required=False, widget=forms.CheckboxSelectMultiple)
    start_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    end_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    is_default = forms.BooleanField(required=False, label="Default calendar of the site")
    notes = forms.CharField(max_length=300, required=False)

    def clean_working_days(self):
        return [int(d) for d in self.cleaned_data["working_days"]]


class HolidayForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    name = forms.CharField(max_length=120)


class ContactForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=150)
    role_title = forms.CharField(max_length=100, required=False, label="Role")
    phone = forms.CharField(max_length=32, required=False)
    email = forms.EmailField(required=False)
    escalation_order = forms.IntegerField(min_value=1, max_value=MAX_ESCALATION_ORDER, required=False,
                                          help_text="1 = contacted first. Leave empty to append at the end.")
    notes = forms.CharField(max_length=300, required=False)

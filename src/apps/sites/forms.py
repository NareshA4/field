import re
import zoneinfo

from django import forms

from apps.ui.forms import BootstrapFormMixin

from .models import MAX_ESCALATION_ORDER, Zone

WEEKDAYS = [(1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"), (5, "Friday"), (6, "Saturday"),
            (7, "Sunday")]

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,29}$")
_PHONE_RE = re.compile(r"^[0-9+\s\-()]{5,32}$")


class SiteForm(BootstrapFormMixin, forms.Form):
    code = forms.CharField(
        max_length=30,
        help_text="Short unique identifier, e.g. PLANT-01 (1-30 chars: uppercase letters, numbers, '.', '_', '-').",
        widget=forms.TextInput(attrs={"placeholder": "e.g. PLANT-01", "style": "text-transform: uppercase;"})
    )
    name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Main Plant & Headquarters"})
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Brief description of the facility"})
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Street address, plot number, industrial area"})
    )
    country = forms.CharField(
        max_length=2,
        required=False,
        help_text="ISO country code, e.g. IN, US, GB",
        widget=forms.TextInput(attrs={"placeholder": "IN", "maxlength": 2, "id": "id_country", "style": "text-transform: uppercase;"})
    )
    state_region = forms.CharField(
        max_length=100,
        required=False,
        label="State / region",
        widget=forms.TextInput(attrs={"placeholder": "Select or type state/region", "id": "id_state_region", "autocomplete": "off"})
    )
    city = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Select or type city", "id": "id_city", "autocomplete": "off"})
    )
    postal_code = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. 560001 or 10001"})
    )
    timezone = forms.CharField(
        max_length=64,
        help_text="IANA name, e.g. Asia/Kolkata, UTC",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Asia/Kolkata", "id": "id_timezone"})
    )
    contact_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. John Doe"})
    )
    contact_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"placeholder": "contact@example.com"})
    )
    contact_phone = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. +91 98765 43210"})
    )

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if not code:
            raise forms.ValidationError("Site code is required.")
        if not _CODE_RE.fullmatch(code):
            raise forms.ValidationError("Site code must be 1-30 characters: letters, digits, '.', '_' or '-'.")
        return code

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Site name is required.")
        return name

    def clean_country(self):
        country = (self.cleaned_data.get("country") or "").strip().upper()
        if country and not re.fullmatch(r"^[A-Z]{2}$", country):
            raise forms.ValidationError("Country must be a 2-letter ISO code (e.g. IN, US, GB).")
        return country

    def clean_timezone(self):
        tz = (self.cleaned_data.get("timezone") or "").strip()
        if not tz:
            raise forms.ValidationError("Timezone is required.")
        try:
            zoneinfo.ZoneInfo(tz)
        except Exception:
            raise forms.ValidationError(f"Unknown timezone '{tz}'.")
        return tz

    def clean_contact_phone(self):
        phone = (self.cleaned_data.get("contact_phone") or "").strip()
        if phone:
            if not _PHONE_RE.fullmatch(phone):
                raise forms.ValidationError("Enter a valid phone number (e.g. +91 98765 43210).")
        return phone

    def clean_postal_code(self):
        postal = (self.cleaned_data.get("postal_code") or "").strip()
        if postal and len(postal) > 20:
            raise forms.ValidationError("Postal code must not exceed 20 characters.")
        return postal

    def clean_city(self):
        return (self.cleaned_data.get("city") or "").strip()

    def clean_state_region(self):
        return (self.cleaned_data.get("state_region") or "").strip()

    def clean_address(self):
        return (self.cleaned_data.get("address") or "").strip()

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

    def clean_contact_name(self):
        return (self.cleaned_data.get("contact_name") or "").strip()


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

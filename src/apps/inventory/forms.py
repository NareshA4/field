from django import forms

from apps.ui.forms import BootstrapFormMixin

from .models import Part, Warehouse

QTY = {"max_digits": 14, "decimal_places": 3}


class _PartChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.part_number} · {obj.name} ({obj.unit})"


class _WarehouseChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} · {obj.name} ({obj.site.code})"


def _level(label):
    return forms.DecimalField(required=False, min_value=0, label=label, **QTY)


class PartForm(BootstrapFormMixin, forms.Form):
    part_number = forms.CharField(max_length=60)
    name = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    unit = forms.CharField(max_length=20, initial="pcs")
    min_stock = _level("Minimum stock")
    max_stock = _level("Maximum stock")
    reorder_quantity = _level("Reorder quantity")


class WarehouseForm(BootstrapFormMixin, forms.Form):
    site = forms.ModelChoiceField(queryset=None, empty_label="Choose a site")
    code = forms.CharField(max_length=20)
    name = forms.CharField(max_length=120)
    description = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, sites=None, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            del self.fields["site"]
        else:
            self.fields["site"].queryset = sites

    @property
    def model(self):
        return Warehouse


class ReceiveForm(BootstrapFormMixin, forms.Form):
    warehouse = _WarehouseChoice(queryset=Warehouse.objects.none(), empty_label="Choose a warehouse")
    part = _PartChoice(queryset=Part.objects.none(), empty_label="Choose a part")
    quantity = forms.DecimalField(min_value=0.001, **QTY)
    reference = forms.CharField(max_length=100, required=False, label="Reference (PO / delivery note)")
    reason = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, warehouses=None, parts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = warehouses
        self.fields["part"].queryset = parts


class AdjustForm(BootstrapFormMixin, forms.Form):
    delta = forms.DecimalField(label="Adjustment (+ / -)", **QTY)
    reason = forms.CharField(max_length=300)


class LevelsForm(BootstrapFormMixin, forms.Form):
    min_level = _level("Minimum level")
    max_level = _level("Maximum level")
    reorder_quantity = _level("Reorder quantity")


class TransferForm(BootstrapFormMixin, forms.Form):
    source = _WarehouseChoice(queryset=Warehouse.objects.none(), empty_label="From warehouse")
    target = _WarehouseChoice(queryset=Warehouse.objects.none(), empty_label="To warehouse")
    part = _PartChoice(queryset=Part.objects.none(), empty_label="Choose a part")
    quantity = forms.DecimalField(min_value=0.001, **QTY)
    reason = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, warehouses=None, parts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = warehouses
        self.fields["target"].queryset = warehouses
        self.fields["part"].queryset = parts


class RequestPartForm(BootstrapFormMixin, forms.Form):
    part = _PartChoice(queryset=Part.objects.none(), empty_label="Choose a part")
    quantity = forms.DecimalField(min_value=0.001, **QTY)
    notes = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, parts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["part"].queryset = parts


class ReserveForm(BootstrapFormMixin, forms.Form):
    warehouse = _WarehouseChoice(queryset=Warehouse.objects.none(), empty_label="Warehouse")
    quantity = forms.DecimalField(min_value=0.001, required=False, help_text="Blank = everything still uncovered", **QTY)

    def __init__(self, *args, warehouses=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = warehouses


class IssueForm(BootstrapFormMixin, forms.Form):
    warehouse = _WarehouseChoice(queryset=Warehouse.objects.none(), empty_label="Warehouse")
    quantity = forms.DecimalField(min_value=0.001, **QTY)

    def __init__(self, *args, warehouses=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = warehouses


class QuantityForm(BootstrapFormMixin, forms.Form):
    quantity = forms.DecimalField(min_value=0.001, required=False, **QTY)
    reason = forms.CharField(max_length=300, required=False)

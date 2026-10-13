import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('incidents', '0002_sync_schema'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicerequest',
            name='failure_category',
            field=models.CharField(blank=True, default='', help_text='Failure category', max_length=100),
        ),
        migrations.AddField(
            model_name='servicerequest',
            name='assigned_team',
            field=models.CharField(blank=True, default='', help_text='Assigned maintenance team', max_length=100),
        ),
        migrations.AddField(
            model_name='servicerequest',
            name='duplicate_of',
            field=models.ForeignKey(
                blank=True,
                help_text='Existing active incident this request was linked to as duplicate',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='linked_requests',
                to='incidents.servicerequest',
            ),
        ),
        migrations.AddField(
            model_name='servicerequest',
            name='override_reason',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Mandatory reason provided when overriding duplicate incident detection',
                max_length=500,
            ),
        ),
        migrations.AddIndex(
            model_name='servicerequest',
            index=models.Index(fields=['organization', 'asset', 'status'], name='incidents_org_asset_status_idx'),
        ),
    ]


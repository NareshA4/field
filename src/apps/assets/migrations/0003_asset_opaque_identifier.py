import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_category_attributes_document_removal'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='opaque_identifier',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False),
        ),
    ]

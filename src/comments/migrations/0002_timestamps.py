import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("comments", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="comment", name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now), preserve_default=False,
        ),
        migrations.AddField(
            model_name="attachment", name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now), preserve_default=False,
        ),
        migrations.AddField(
            model_name="attachment", name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now), preserve_default=False,
        ),
    ]

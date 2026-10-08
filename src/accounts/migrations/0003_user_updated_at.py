import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("accounts", "0002_user_is_guest")]

    operations = [
        migrations.AddField(
            model_name="user", name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now), preserve_default=False,
        ),
    ]

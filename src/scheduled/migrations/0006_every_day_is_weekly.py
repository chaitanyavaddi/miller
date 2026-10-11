"""Every day is gone as a rhythm: it's weekly on all seven days, which says the same thing."""
from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("scheduled", "ScheduledItem").objects.filter(rhythm="daily").update(rhythm="weekly", weekdays="0,1,2,3,4,5,6")


class Migration(migrations.Migration):
    dependencies = [("scheduled", "0005_drop_month")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

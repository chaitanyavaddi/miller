"""Scheduled keeps four rhythms: once, every day, weekly, monthly. Weekdays becomes weekly on
Monday to Friday; yearly becomes monthly on the same day (none exist yet; this keeps any safe)."""
from django.db import migrations


def forwards(apps, schema_editor):
    ScheduledItem = apps.get_model("scheduled", "ScheduledItem")
    ScheduledItem.objects.filter(rhythm="weekdays").update(rhythm="weekly", weekdays="0,1,2,3,4")
    ScheduledItem.objects.filter(rhythm="yearly").update(rhythm="monthly")


class Migration(migrations.Migration):
    dependencies = [("scheduled", "0003_remove_scheduleditem_time")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

# Generated by Django 2.2.11 on 2020-03-18 21:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0009_auto_20200318_2134'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hub',
            name='slug',
            field=models.CharField(blank=True, max_length=256, null=True, unique=True),
        ),
    ]

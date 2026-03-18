# Generated migration for is_audio_analyzed field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_add_external_data_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='track',
            name='is_audio_analyzed',
            field=models.BooleanField(
                default=True,
                help_text='True if audio features from Spotify, False if using neutral defaults'
            ),
        ),
        migrations.AddIndex(
            model_name='track',
            index=models.Index(fields=['is_audio_analyzed'], name='catalog_tra_is_audi_idx'),
        ),
    ]

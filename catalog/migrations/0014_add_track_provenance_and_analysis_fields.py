"""Track provenance and local audio-analysis state.

Adds the fields needed to compute audio features locally instead of fetching
them from Spotify: where a track came from, its preview clip, and which
extractor version last analysed it.

The RenameIndex is unrelated pre-existing drift - the index created in 0013
was given an explicit name that no longer matches what Django derives - and is
included here only because makemigrations emits it alongside the real changes.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0013_add_precomputed_recommendation'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='precomputedrecommendation',
            new_name='catalog_pre_source__c3dd26_idx',
            old_name='catalog_pre_source__idx',
        ),
        migrations.AddField(
            model_name='track',
            name='analysis_version',
            field=models.IntegerField(db_index=True, default=0, help_text='Version of the extractor that produced the audio features; 0 = never analysed locally'),
        ),
        migrations.AddField(
            model_name='track',
            name='analyzed_at',
            field=models.DateTimeField(blank=True, help_text='When audio features were last computed from the preview clip', null=True),
        ),
        migrations.AddField(
            model_name='track',
            name='isrc',
            field=models.CharField(blank=True, db_index=True, help_text='International Standard Recording Code, stable across providers', max_length=15, null=True),
        ),
        migrations.AddField(
            model_name='track',
            name='preview_url',
            field=models.URLField(blank=True, help_text='Provider preview clip (~30s) used for audio analysis. Deezer signs these with an expiry, so treat a stored value as stale and refetch.', max_length=1000, null=True),
        ),
        migrations.AddField(
            model_name='track',
            name='source',
            field=models.CharField(choices=[('legacy', 'Legacy import'), ('csv', 'CSV dataset'), ('spotify', 'Spotify API'), ('deezer', 'Deezer API'), ('seed', 'Seed data')], db_index=True, default='legacy', help_text='Which provider this track was ingested from', max_length=20),
        ),
        migrations.AddIndex(
            model_name='track',
            index=models.Index(fields=['is_audio_analyzed', 'analysis_version'], name='catalog_tra_is_audi_43ac20_idx'),
        ),
    ]

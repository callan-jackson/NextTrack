"""Forms for search, preferences, and feedback validation."""

from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator


class PreferenceForm(forms.Form):
    """Validates audio feature preference sliders (0.0-1.0)."""

    energy = forms.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        widget=forms.NumberInput(attrs={
            'type': 'range',
            'min': '0',
            'max': '1',
            'step': '0.1',
            'class': 'preference-slider',
            'id': 'energy-slider'
        }),
        help_text='0 = calm, 1 = intense'
    )

    valence = forms.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        widget=forms.NumberInput(attrs={
            'type': 'range',
            'min': '0',
            'max': '1',
            'step': '0.1',
            'class': 'preference-slider',
            'id': 'valence-slider'
        }),
        help_text='0 = sad, 1 = happy'
    )

    danceability = forms.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        widget=forms.NumberInput(attrs={
            'type': 'range',
            'min': '0',
            'max': '1',
            'step': '0.1',
            'class': 'preference-slider',
            'id': 'danceability-slider'
        }),
        help_text='0 = still, 1 = danceable'
    )

    acousticness = forms.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        widget=forms.NumberInput(attrs={
            'type': 'range',
            'min': '0',
            'max': '1',
            'step': '0.1',
            'class': 'preference-slider',
            'id': 'acousticness-slider'
        }),
        help_text='0 = electronic, 1 = acoustic'
    )

    def clean(self):
        """Build a preferences dict from non-None fields."""
        cleaned_data = super().clean()

        energy = cleaned_data.get('energy')
        valence = cleaned_data.get('valence')
        danceability = cleaned_data.get('danceability')
        acousticness = cleaned_data.get('acousticness')

        preferences = {}
        if energy is not None:
            preferences['energy'] = energy
        if valence is not None:
            preferences['valence'] = valence
        if danceability is not None:
            preferences['danceability'] = danceability
        if acousticness is not None:
            preferences['acousticness'] = acousticness

        cleaned_data['preferences'] = preferences

        return cleaned_data

    def get_preferences(self):
        """Return validated preferences dict."""
        if not self.is_valid():
            return {}
        return self.cleaned_data.get('preferences', {})


class SearchForm(forms.Form):
    """Validates and sanitizes search input."""

    query = forms.CharField(
        min_length=2,
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'Search by song title or artist...',
            'class': 'search-input',
            'autocomplete': 'off',
            'autofocus': True,
            'id': 'search-query'
        }),
        error_messages={
            'min_length': 'Search query must be at least 2 characters.',
            'max_length': 'Search query cannot exceed 200 characters.',
            'required': 'Please enter a search query.'
        }
    )

    def clean_query(self):
        """Strip and normalize whitespace in query."""
        query = self.cleaned_data.get('query', '')
        query = ' '.join(query.split())

        if len(query) < 2:
            raise forms.ValidationError('Search query must be at least 2 characters.')

        return query


class FeedbackForm(forms.Form):
    """Validates like/dislike feedback on a track."""

    track_id = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.HiddenInput(),
        error_messages={
            'required': 'Track ID is required for feedback submission.'
        }
    )

    score = forms.BooleanField(
        required=False,
        widget=forms.HiddenInput()
    )

    def clean_track_id(self):
        """Validate track_id format."""
        track_id = self.cleaned_data.get('track_id', '').strip()

        if not track_id:
            raise forms.ValidationError('Track ID cannot be empty.')

        if not track_id.replace('-', '').replace('_', '').isalnum():
            raise forms.ValidationError('Invalid track ID format.')

        return track_id

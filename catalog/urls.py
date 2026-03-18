"""URL routing for the Catalog REST API."""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from catalog.views import GenreViewSet, ArtistViewSet, TrackViewSet, SurveyAPIView, AnalyticsAPIView, FeedbackAPIView, RecommendationMetricsView

router = DefaultRouter()
router.register(r'genres', GenreViewSet, basename='genre')
router.register(r'artists', ArtistViewSet, basename='artist')
router.register(r'tracks', TrackViewSet, basename='track')

urlpatterns = [
    path('', include(router.urls)),
    path('feedback/', FeedbackAPIView.as_view(), name='submit_feedback'),
    path('survey/', SurveyAPIView.as_view(), name='survey'),
    path('analytics/', AnalyticsAPIView.as_view(), name='analytics'),
    path('metrics/', RecommendationMetricsView.as_view(), name='metrics'),
]

"""Load testing scenarios for NextTrack."""

from locust import HttpUser, task, between


class SearchUser(HttpUser):
    """Simulates users searching for music."""
    wait_time = between(2, 5)

    @task(3)
    def search_tracks(self):
        queries = ['rock', 'jazz', 'pop', 'electronic', 'classical', 'hip hop', 'indie']
        import random
        q = random.choice(queries)
        self.client.get(f'/api/tracks/search/?q={q}')

    @task(1)
    def browse_tracks(self):
        self.client.get('/api/tracks/')


class RecommendationUser(HttpUser):
    """Simulates users requesting recommendations."""
    wait_time = between(5, 15)

    @task
    def get_recommendations(self):
        self.client.post('/api/tracks/recommend/', json={
            'track_ids': ['4uLU6hMCjMI75M1A2tKUQC', '3n3Ppam7vgaVa1iaRUc9Lp'],
            'preferences': {'energy': 0.7},
            'limit': 5,
        })


class BrowseUser(HttpUser):
    """Simulates users browsing the library."""
    wait_time = between(3, 10)

    @task(2)
    def view_statistics(self):
        self.client.get('/api/tracks/statistics/')

    @task(3)
    def browse_genres(self):
        self.client.get('/api/genres/')

    @task(1)
    def health_check(self):
        self.client.get('/health/')

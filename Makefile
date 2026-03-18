.PHONY: test test-cov lint migrate shell dev security-scan db-health load-test

# Run the full test suite
test:
	python manage.py test catalog --verbosity=2

# Run tests with coverage report
test-cov:
	coverage run manage.py test catalog --verbosity=2
	coverage report
	coverage html

# Lint with ruff
lint:
	ruff check catalog/ next_track/

# Apply database migrations
migrate:
	python manage.py migrate

# Open Django interactive shell
shell:
	python manage.py shell

# Start the development server
dev:
	python manage.py runserver 0.0.0.0:8000

# Security scanning (pip-audit + bandit)
security-scan:
	pip-audit -r requirements.txt || true
	bandit -r catalog/ next_track/ -ll || true

# Database health check
db-health:
	python manage.py db_health

# Load testing with Locust
load-test:
	locust -f locustfile.py --host=http://localhost:8000

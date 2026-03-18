#!/usr/bin/env bash
set -euo pipefail

# Blue-green deployment script for NextTrack
# Usage: ./scripts/deploy.sh [blue|green]

COMPOSE_FILE="docker-compose.prod.yml"
COLOR="${1:-blue}"
HEALTH_URL="http://localhost:8000/health/"

echo "=== NextTrack Blue-Green Deploy: $COLOR ==="

# 1. Build new image
echo "[1/5] Building new image..."
docker-compose -f "$COMPOSE_FILE" build web

# 2. Run migrations
echo "[2/5] Running migrations..."
docker-compose -f "$COMPOSE_FILE" run --rm web python manage.py migrate --noinput

# 3. Collect static files
echo "[3/5] Collecting static files..."
docker-compose -f "$COMPOSE_FILE" run --rm web python manage.py collectstatic --noinput

# 4. Rolling restart
echo "[4/5] Performing rolling restart..."
docker-compose -f "$COMPOSE_FILE" up -d --no-deps web celery

# 5. Health check
echo "[5/5] Waiting for health check..."
for i in $(seq 1 30); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        echo "Health check passed after $i seconds!"
        exit 0
    fi
    sleep 1
done

echo "WARNING: Health check failed after 30s!"
echo "Consider rolling back: docker-compose -f $COMPOSE_FILE down && git checkout HEAD~1"
exit 1

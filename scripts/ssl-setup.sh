#!/usr/bin/env bash
set -euo pipefail

# SSL certificate setup via Let's Encrypt (certbot)
# Usage: ./scripts/ssl-setup.sh <domain>

DOMAIN="${1:?Usage: $0 <domain>}"
NGINX_CONF_DIR="./nginx/conf.d"

echo "=== SSL Setup for $DOMAIN ==="

# Check for certbot
if ! command -v certbot &> /dev/null; then
    echo "Installing certbot..."
    pip install certbot certbot-nginx || {
        echo "Failed to install certbot. Install manually:"
        echo "  apt-get install certbot python3-certbot-nginx"
        exit 1
    }
fi

# Obtain certificate
echo "Obtaining certificate for $DOMAIN..."
certbot certonly \
    --webroot \
    --webroot-path=/var/www/html \
    --email "admin@${DOMAIN}" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN" \
    -d "www.${DOMAIN}"

# Copy certs to nginx directory
mkdir -p ./nginx/certs
cp "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ./nginx/certs/
cp "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ./nginx/certs/

echo "SSL certificates installed. Restart nginx to apply:"
echo "  docker-compose -f docker-compose.prod.yml restart nginx"

# Add renewal cron
echo "0 0 1 * * certbot renew --quiet && docker-compose -f docker-compose.prod.yml restart nginx" | crontab -
echo "Monthly renewal cron job added."

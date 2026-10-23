"""Exact website origins permitted to read this API from a browser."""
import os
from urllib.parse import urlsplit


def allowed_origins():
    production = os.environ.get('APP_ENV', 'development') == 'production'
    configured = os.environ.get('CORS_ALLOWED_ORIGINS', '')
    if not configured.strip():
        if production:
            raise ValueError('CORS_ALLOWED_ORIGINS must list the production website address.')
        return ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:3000']
    origins = []
    for raw in configured.split(','):
        origin = raw.strip().rstrip('/')
        parsed = urlsplit(origin)
        if (not parsed.hostname or parsed.scheme not in ('https', 'http') or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or '*' in origin):
            raise ValueError('CORS_ALLOWED_ORIGINS must contain exact HTTP(S) origins without paths or wildcards.')
        if production and parsed.scheme != 'https':
            raise ValueError('Production website origins must use HTTPS.')
        if origin not in origins:
            origins.append(origin)
    return origins

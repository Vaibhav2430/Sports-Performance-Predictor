"""Server-only admin bearer token. Bulk operations are disabled if unset."""
import os
import secrets
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer = HTTPBearer(auto_error=False)


def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    expected = os.environ.get('ADMIN_API_TOKEN', '')
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail='Admin operations are disabled until an admin token is configured.')
    supplied = credentials.credentials if credentials and credentials.scheme.lower() == 'bearer' else ''
    if len(supplied) > 512 or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail='Valid admin authorization is required.', headers={'WWW-Authenticate': 'Bearer'})

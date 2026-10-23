"""Provision a read-only export login; store its URL only in private .env."""
import os
from pathlib import Path
import secrets

from dotenv import set_key
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import database


def configure_readonly_login():
    path = Path(__file__).with_name('.env')
    existing = os.environ.get('BACKUP_DATABASE_URL')
    if existing:
        return existing
    original = conninfo_to_dict(os.environ['DATABASE_URL'])
    role = 'courtcast_backup_' + secrets.token_hex(6)
    password = secrets.token_urlsafe(32)
    with database.connect() as connection:
        connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS').format(sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA courtcast TO {}').format(sql.Identifier(role)))
        for table in ('predictions', 'documents', 'budgets', 'snapshots'):
            connection.execute(sql.SQL('GRANT SELECT ON courtcast.{} TO {}').format(sql.Identifier(table), sql.Identifier(role)))
            connection.execute(sql.SQL('CREATE POLICY {} ON courtcast.{} FOR SELECT TO {} USING (true)').format(sql.Identifier(role), sql.Identifier(table), sql.Identifier(role)))
    # Supabase's Session pooler encodes the project reference in the login.
    suffix = '.' + original['user'].split('.', 1)[1] if '.pooler.supabase.com' in original.get('host', '') else ''
    url = make_conninfo(os.environ['DATABASE_URL'], user=role + suffix, password=password)
    set_key(str(path), 'BACKUP_DATABASE_URL', url)
    print('Read-only backup login created; private connection saved.')
    return url


if __name__ == '__main__':
    try:
        configure_readonly_login()
    except Exception:
        raise SystemExit('Could not configure the backup login; no credentials displayed.')

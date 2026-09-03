#!/bin/sh
set -eu

# Mem0 keeps vector data in POSTGRES_DB and its API/auth configuration in a
# separate application database. PostgreSQL only runs this file when the volume
# is initialized for the first time.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE mem0_app'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mem0_app')\gexec
EOSQL

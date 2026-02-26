#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE USER archivemodule WITH PASSWORD 'changethis' CREATEDB;
	CREATE DATABASE chatarchive OWNER archivemodule;
EOSQL
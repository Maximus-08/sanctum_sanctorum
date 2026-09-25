"""Engine configuration rules.

These cover the SQLite/Postgres differences that only show up at deployment time, so they
assert on the parsed URL and the engine arguments rather than opening a connection.
"""
import pytest

from app.db import engine_options, normalize_database_url

SQLITE_URL = "sqlite:///./sanctum.db"
HOSTED_URL = "postgres://sanctum:s3cret@db.example.com:5432/sanctum"


class TestNormalizeDatabaseUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "postgres://user:pw@host/db",  # what Supabase, Neon and Heroku hand out
            "postgresql://user:pw@host/db",  # valid, but resolves to psycopg2 by default
        ],
    )
    def test_postgres_aliases_map_to_the_installed_driver(self, url):
        assert normalize_database_url(url).drivername == "postgresql+psycopg"

    def test_an_explicit_driver_is_left_alone(self):
        url = normalize_database_url("postgresql+psycopg2://user:pw@host/db")
        assert url.drivername == "postgresql+psycopg2"

    def test_sqlite_url_is_unchanged(self):
        assert normalize_database_url(SQLITE_URL).render_as_string(hide_password=False) == SQLITE_URL

    def test_credentials_and_target_survive_normalization(self):
        url = normalize_database_url(HOSTED_URL)
        assert (url.username, url.password) == ("sanctum", "s3cret")
        assert (url.host, url.port, url.database) == ("db.example.com", 5432, "sanctum")


class TestEngineOptions:
    def test_sqlite_disables_the_same_thread_check(self):
        options = engine_options(normalize_database_url(SQLITE_URL))
        assert options["connect_args"] == {"check_same_thread": False}

    def test_postgres_does_not_receive_sqlite_connect_args(self):
        # ``check_same_thread`` is a sqlite3 argument; Postgres drivers reject it.
        assert "connect_args" not in engine_options(normalize_database_url(HOSTED_URL))

    def test_postgres_pools_are_checked_before_use(self):
        # Hosted Postgres closes idle connections, which otherwise surface as a 500.
        assert engine_options(normalize_database_url(HOSTED_URL))["pool_pre_ping"] is True

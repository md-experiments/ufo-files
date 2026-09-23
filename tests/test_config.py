from pathlib import Path

from ufo.config import _database_url


def test_database_url(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _database_url(tmp_path) == f"sqlite:///{tmp_path / 'ufo.db'}"
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert _database_url(Path(".")) == "postgresql+psycopg://u:p@host:5432/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    assert _database_url(Path(".")) == "postgresql+psycopg://u:p@host/db"

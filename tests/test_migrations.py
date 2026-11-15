"""Migrations must build exactly the schema the models describe, and be reversible."""
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_upgrade_match_models_and_downgrade(monkeypatch):
    db = Path(tempfile.gettempdir()) / "dispatch_migrations_test.db"
    db.unlink(missing_ok=True)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))

    command.upgrade(cfg, "head")
    # Fails if a model changed without a matching migration
    command.check(cfg)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

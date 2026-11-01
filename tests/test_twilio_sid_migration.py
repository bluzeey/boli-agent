from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.config import Settings


def test_sid_migration_preserves_legacy_rows_without_guessing_ids(tmp_path, monkeypatch):
    import app.config as config

    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(config, "get_settings", lambda: Settings(database_url=url))
    root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(root / "alembic.ini"))
    command.upgrade(alembic_config, "a1b2c3d4e5f6")
    engine = sa.create_engine(url)
    legacy = sa.Table("vendor_responses", sa.MetaData(), autoload_with=engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        for response_id in ("legacy-one", "legacy-two"):
            connection.execute(legacy.insert().values(
                id=response_id, case_id="synthetic-case", vendor_id="synthetic-vendor",
                rfq_id="synthetic-rfq", rfq_version=1, channel="whatsapp", status="sent",
                message_text="synthetic", attempts=1, created_at=now, updated_at=now,
            ))
    command.upgrade(alembic_config, "head")
    current = sa.Table("vendor_responses", sa.MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        assert connection.execute(sa.select(
            current.c.id, current.c.twilio_message_sid
        ).order_by(current.c.id)).all() == [("legacy-one", None), ("legacy-two", None)]
        connection.execute(current.update().where(current.c.id == "legacy-one").values(
            twilio_message_sid="SM" + "1" * 32
        ))
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(current.update().where(current.c.id == "legacy-two").values(
            twilio_message_sid="SM" + "1" * 32
        ))
    command.downgrade(alembic_config, "a1b2c3d4e5f6")
    assert "twilio_message_sid" not in {
        column["name"] for column in sa.inspect(engine).get_columns("vendor_responses")
    }
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM vendor_responses")) == 2
    engine.dispose()

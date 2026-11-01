import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session
from starlette.requests import Request

from alembic import command
from app.browser_access import OWNER_COOKIE, owner_digest, require_private_case
from app.config import Settings
from app.models import BrowserOwner, BrowserSession, Conversation, ProcurementCase


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_private_session_migration_preserves_legacy_and_enforces_new_owners(
    tmp_path,
    monkeypatch,
    backend,
):
    import app.config as config

    if backend == "postgres":
        url = os.environ.get("BOLI_PG_TEST_URL")
        if not url:
            pytest.skip("Disposable PostgreSQL test database not configured")
        parsed = urlsplit(url.replace("postgresql+psycopg:", "postgresql:"))
        assert parsed.path == "/boli_private_test"
        assert parse_qs(parsed.query)["host"][0].startswith("/private/tmp/")
    else:
        url = f"sqlite:///{tmp_path / 'private-migration.db'}"
    monkeypatch.setattr(config, "get_settings", lambda: Settings(database_url=url))
    root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(root / "alembic.ini"))
    command.upgrade(alembic_config, "b6e39f8a2c10")
    engine = sa.create_engine(url)
    old_vendor = sa.Table("vendors", sa.MetaData(), autoload_with=engine)
    now = datetime.now(UTC)
    legacy = dict(
        id="legacy-vendor",
        external_id="same-shop",
        name="Synthetic legacy shop",
        provider="test",
        category="generic",
        contact_consent=True,
        opted_out=False,
        created_at=now,
        updated_at=now,
    )
    with engine.begin() as connection:
        connection.execute(old_vendor.insert().values(**legacy))
        before = dict(connection.execute(sa.select(old_vendor)).mappings().one())
    command.upgrade(alembic_config, "head")
    current = sa.Table("vendors", sa.MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        after = dict(connection.execute(sa.select(current)).mappings().one())
        assert after.pop("owner_scope") is None
        assert after == before
        assert connection.scalar(sa.text("SELECT count(*) FROM browser_owners")) == 0
        assert connection.scalar(sa.text("SELECT count(*) FROM browser_sessions")) == 0
    alice_token, bob_token = "a" * 43, "b" * 43
    alice, bob = owner_digest(alice_token), owner_digest(bob_token)
    with Session(engine) as session:
        session.add_all(
            [BrowserOwner(token_hash=x, expires_at=now + timedelta(days=1)) for x in [alice, bob]]
        )
        session.flush()
        session.add_all(
            [
                BrowserSession(id="a" * 32, owner_hash=alice),
                BrowserSession(id="b" * 32, owner_hash=bob),
            ]
        )
        session.flush()
        conversation = Conversation(whatsapp_user_id="browser:" + "a" * 32)
        session.add(conversation)
        session.flush()
        case = ProcurementCase(
            conversation_id=conversation.id, raw_request="synthetic private request"
        )
        session.add(case)
        session.commit()
        case_id = case.id

        def request(token):
            return Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/cases/" + case_id,
                    "headers": [(b"cookie", (OWNER_COOKIE + "=" + token).encode())],
                }
            )

        require_private_case(case_id, request(alice_token), session)
        with pytest.raises(Exception) as failure:
            require_private_case(case_id, request(bob_token), session)
        assert failure.value.status_code == 404
    with engine.begin() as connection:
        for scope in [alice, bob]:
            connection.execute(
                current.insert().values(
                    **{**legacy, "id": "vendor-" + scope[:8], "owner_scope": scope}
                )
            )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            current.insert().values(**{**legacy, "id": "duplicate-owner", "owner_scope": alice})
        )
    with pytest.raises(RuntimeError, match="ownership"):
        command.downgrade(alembic_config, "b6e39f8a2c10")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM vendors")) == 3
        assert connection.scalar(sa.text("SELECT count(*) FROM browser_owners")) == 2
    engine.dispose()

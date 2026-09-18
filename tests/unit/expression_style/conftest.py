import pytest

from slim_guard.db.session import Database


@pytest.fixture
async def style_db(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'styles.db'}")
    await db.migrate()
    yield db
    await db.close()

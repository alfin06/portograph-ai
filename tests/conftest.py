import pytest
from core.config import SCHEMA_PATH
from database.db_manager import DBManager


@pytest.fixture
def db(tmp_path):
    """Fresh database per test; never touches database/portograph.db."""
    return DBManager(db_path=str(tmp_path / "test.db"), schema_path=SCHEMA_PATH)

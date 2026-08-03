import shutil
import sys
from collections.abc import Generator
from pathlib import Path
from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy.orm import sessionmaker

# Add src to Python path for direct imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import settings
from utils.logger_util import setup_logging

from src.api.services.semantic_cache_service import semantic_cache

# Import registers the test-only table (`substack_test`) on its own Base.metadata,
# used by ensure_test_tables below.
from test_models.test_sql_models import Base as TestBase

logger = setup_logging()
db = settings.supabase_db

# quote_plus escapes special characters (e.g. "@") that can appear in a real
# database password so they don't break the connection URL.
DATABASE_URL = (
    f"postgresql://{db.user}:{quote_plus(db.password.get_secret_value())}"
    f"@{db.host}:{db.port}/{db.name}"
)


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    """Create a SQLAlchemy engine for the test database session, creating any
    test-only tables (e.g. `substack_test`) that don't already exist.

    Deliberately NOT autouse: only tests marked `integration` request this
    fixture (directly or via `db_session`), so a real DB connection is only
    attempted when integration tests are actually selected — running
    `pytest -m "not integration"` (as CI's unmocked `test` job does) never
    triggers this fixture and needs no live database.

    Args:
        None
    Yields:
        Engine: A SQLAlchemy engine connected to the test database.
    """
    logger.info("Creating test database engine")
    engine = create_engine(DATABASE_URL)
    logger.info("Ensuring test-only tables exist")
    TestBase.metadata.create_all(bind=engine)
    yield engine
    logger.info("Disposing test database engine")
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine: Engine) -> Generator[SQLAlchemySession, None, None]:
    """Provide a SQLAlchemy session for a single test function.
    Closes the session after the test finishes.

    Args:
        db_engine (Engine): The SQLAlchemy engine to bind the session to.
    Yields:
        SQLAlchemySession: A SQLAlchemy session connected to the test database.
    """
    logger.info("Creating test database session")
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()
    logger.info("Closed test database session")


@pytest.fixture(scope="function", autouse=True)
def clear_prefect_cache() -> Generator[None, None, None]:
    """Automatically clear Prefect cache before and after each test function
    to prevent interference between tests.

    Args:
        None
    Yields:
        None
    """
    prefect_dir = Path(".prefect")
    logger.debug("Clearing Prefect cache before test")
    if prefect_dir.exists():
        shutil.rmtree(prefect_dir, ignore_errors=True)
    yield
    if prefect_dir.exists():
        shutil.rmtree(prefect_dir, ignore_errors=True)
    logger.debug("Cleared Prefect cache after test")


@pytest.fixture(scope="function", autouse=True)
def clear_semantic_cache() -> Generator[None, None, None]:
    """Clear the module-level semantic cache singleton before and after each
    test function, so a cache entry populated by one test can't produce a
    surprise hit (or a false miss) in another.
    """
    semantic_cache.clear()
    yield
    semantic_cache.clear()

"""
Test configuration and shared fixtures.
"""

import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

# Override settings before any app imports
import os
os.environ["GOOGLE_PLACES_API_KEY"] = "test-key-for-testing"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"
os.environ["DATABASE_URL_SYNC"] = "sqlite:///test.db"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

import pytest
import os

# Use test environment — no real DB/API calls needed
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/cryptomind_test")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")
os.environ.setdefault("KRAKEN_API_KEY", "test_key")
os.environ.setdefault("KRAKEN_SECRET_KEY", "test_secret")
os.environ.setdefault("TOTAL_CAPITAL_USD", "1000.0")

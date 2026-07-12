import pytest
from dotenv import load_dotenv

from app.supply import provider

load_dotenv()


@pytest.fixture(autouse=True)
def _reset_supply_simulation():
    """_FLIGHTS/_HOTELS/_ACTIVITIES/_unavailable_ids are module-level
    singletons shared across the whole pytest session -- any test calling
    provider.simulate_* would otherwise leak into every later test."""
    yield
    provider.simulate_reset()

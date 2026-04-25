import pytest
from ocm.tools import checkpoint as _cp_module


@pytest.fixture(autouse=True)
def _reset_checkpoint_db():
    """Reset the checkpoint module-level DB singleton after every test.

    test_checkpoint.py injects a test DB via cp_module._db = db. Without
    cleanup this leaks into subsequent tests that expect Database.for_project()
    to resolve the DB from OCM_PROJECT_DIR instead.
    """
    yield
    _cp_module._db = None

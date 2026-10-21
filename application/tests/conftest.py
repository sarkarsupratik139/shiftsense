import pytest

from shiftsense import baseline, data_gen, optimizer, staffing
from shiftsense.config import load_policy


@pytest.fixture(scope="session")
def policy():
    return load_policy()


@pytest.fixture(scope="session")
def data(policy):
    return data_gen.generate(policy)


@pytest.fixture(scope="session")
def req(policy, data):
    return staffing.staffing_chain(policy, staffing.forecast(policy, data["history"]))["req"]


@pytest.fixture(scope="session")
def heuristic_plan(policy, data, req):
    return baseline.plan(policy, data["agents"], data["leave"], req)


@pytest.fixture(scope="session")
def optimized_plan(policy, data, req):
    return optimizer.plan(policy, data["agents"], data["leave"], req)


@pytest.fixture(autouse=True, scope="session")
def _isolated_store(tmp_path_factory):
    """Never touch the real demo database from tests."""
    import os
    os.environ["SHIFTSENSE_DB"] = str(tmp_path_factory.mktemp("db") / "test.db")
    os.environ.setdefault("SHIFTSENSE_PBKDF2_ITERATIONS", "1000")     # fast hashing in tests (production: 600,000)
    yield



@pytest.fixture(autouse=True)
def _fresh_ai_memo():
    """The assistant remembers identical AI answers; tests must not see each other's."""
    from shiftsense import assistant
    assistant._MEMO.clear()
    yield

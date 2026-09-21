from pathlib import Path

import pytest
from PIL import Image

from typesafe_computer_use.models import Item, Screen


@pytest.fixture
def screen() -> Screen:
    return Screen(image=Image.new("RGB", (2000, 1200)), scale=2.0, app="Google Chrome", field=None, url=None)


def item(index: int, text: str, x1=100, y1=100, x2=400, y2=130, conf=1.0) -> Item:
    return Item(index, text, conf, x1, y1, x2, y2)


@pytest.fixture
def make_item():
    return item


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLICKER_TEST_KEY", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def isolate_test_input_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure every test runs with an isolated input lock path under its own tmp_path.

    Guarantees the test suite never touches or clears the production lock at /tmp/typesafe_input_locked.
    """
    from typesafe_computer_use import macos
    from typesafe_computer_use.worker.gate import ExecutionGate

    test_lock = tmp_path / "typesafe_input_locked"
    monkeypatch.setenv("TYPESAFE_INPUT_LOCK_PATH", str(test_lock))
    macos.set_input_lock(False)
    ExecutionGate.reset_instance()
    yield test_lock
    macos.set_input_lock(False)
    ExecutionGate.reset_instance()
    if test_lock.exists():
        test_lock.unlink(missing_ok=True)



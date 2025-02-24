# test_persistence.py

from pathlib import Path
import pytest
from unittest.mock import patch
from filelock import Timeout

from src.comfydock_core.persistence import load_environments, save_environments, PersistenceError


@pytest.fixture
def temp_files(tmp_path):
    """Fixture to provide temporary file paths for the database and lock file."""
    db_file = tmp_path / "test_environments.json"
    lock_file = tmp_path / "test_environments.json.lock"
    return str(db_file), str(lock_file)


def test_load_nonexistent_file_returns_empty_list(temp_files):
    """
    If the database file does not exist, load_environments should return an empty list.
    """
    db_file, lock_file = temp_files
    # Ensure the file does not exist
    assert not Path(db_file).exists()
    envs = load_environments(db_file=db_file, lock_file=lock_file)
    assert envs == []


def test_save_and_load_environments(temp_files):
    """
    Save a list of environment dictionaries and then load them back.
    """
    db_file, lock_file = temp_files
    data = [{"id": "env1", "name": "Test Environment"}]
    save_environments(data, db_file=db_file, lock_file=lock_file)
    loaded = load_environments(db_file=db_file, lock_file=lock_file)
    assert loaded == data


def test_load_corrupt_json_raises_error(temp_files):
    """
    Write invalid JSON to the file and ensure load_environments raises a PersistenceError.
    """
    db_file, lock_file = temp_files
    with open(db_file, "w") as f:
        f.write("this is not valid JSON")
    with pytest.raises(PersistenceError) as excinfo:
        load_environments(db_file=db_file, lock_file=lock_file)
    assert "Error decoding JSON" in str(excinfo.value)


def test_lock_timeout_raises_error(temp_files):
    """
    Simulate a file lock Timeout and ensure load_environments raises a PersistenceError.
    This uses unittest.mock.patch to force FileLock.__enter__ to raise a Timeout.
    """
    db_file, lock_file = temp_files
    with patch("filelock.FileLock.__enter__", side_effect=Timeout("Timeout raised")):
        with pytest.raises(PersistenceError) as excinfo:
            load_environments(db_file=db_file, lock_file=lock_file)
        assert "Could not acquire file lock for loading environments" in str(excinfo.value)

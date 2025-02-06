# test_user_settings.py

import json
from pathlib import Path
import pytest

# To run: uv run pytest .\tests\test_user_settings.py

# Import the module we want to test.
from src.comfy_env_core import user_settings

# Use a fixture to override the file paths in user_settings with temporary ones.
@pytest.fixture
def temp_settings_dir(tmp_path):

    # Save original global values.
    original_settings_file = user_settings.USER_SETTINGS_FILE
    original_lock_file = user_settings.USER_SETTINGS_LOCK_FILE

    # Create a temporary directory and file paths.
    temp_dir = tmp_path / "settings"
    temp_dir.mkdir()
    temp_settings_file = temp_dir / "user.settings.json"
    temp_lock_file = temp_dir / "user.settings.json.lock"

    # Override the module-level variables.
    user_settings.USER_SETTINGS_FILE = str(temp_settings_file)
    user_settings.USER_SETTINGS_LOCK_FILE = str(temp_lock_file)

    yield temp_dir

    # Restore original global values after the test.
    user_settings.USER_SETTINGS_FILE = original_settings_file
    user_settings.USER_SETTINGS_LOCK_FILE = original_lock_file

def test_load_default_settings(temp_settings_dir):
    """
    If no settings file exists, load_user_settings should return default settings.
    """
    default_path = "/default/path"
    settings = user_settings.load_user_settings(default_path)
    assert settings.comfyui_path == default_path
    assert settings.port == 8188
    assert settings.runtime == "nvidia"
    assert settings.command == ""
    assert settings.folders == []
    assert settings.max_deleted_environments == 10

def test_save_and_load_settings(temp_settings_dir):
    """
    Test that saving settings and then loading them returns the same data.
    """
    default_path = "/my/path"
    settings = user_settings.UserSettings(
        comfyui_path=default_path,
        port=8000,
        runtime="cpu",
        command="run",
        folders=[user_settings.Folder(id="folder1", name="Folder One")],
        max_deleted_environments=5
    )
    user_settings.save_user_settings(settings)
    loaded_settings = user_settings.load_user_settings(default_path)
    assert loaded_settings.comfyui_path == settings.comfyui_path
    assert loaded_settings.port == settings.port
    assert loaded_settings.runtime == settings.runtime
    assert loaded_settings.command == settings.command
    assert len(loaded_settings.folders) == 1
    assert loaded_settings.folders[0].id == "folder1"
    assert loaded_settings.folders[0].name == "Folder One"
    assert loaded_settings.max_deleted_environments == settings.max_deleted_environments

def test_update_settings(temp_settings_dir):
    """
    Test that update_user_settings correctly updates the settings.
    """
    default_path = "/initial/path"
    # Ensure we start with default settings.
    settings = user_settings.load_user_settings(default_path)
    # Define new values.
    new_values = {
        "comfyui_path": "/updated/path",
        "port": 9000,
        "runtime": "cpu",
        "command": "start",
        "max_deleted_environments": 7
    }
    updated_settings = user_settings.update_user_settings(new_values)
    assert updated_settings.comfyui_path == "/updated/path"
    assert updated_settings.port == 9000
    assert updated_settings.runtime == "cpu"
    assert updated_settings.command == "start"
    assert updated_settings.max_deleted_environments == 7

def test_corrupt_settings_file(temp_settings_dir):
    """
    Create a corrupt JSON settings file and ensure load_user_settings raises a UserSettingsError.
    """
    default_path = "/corrupt/path"
    settings_file_path = Path(user_settings.USER_SETTINGS_FILE)
    # Write invalid JSON to the file.
    with open(settings_file_path, "w") as f:
        f.write("this is not valid JSON")
    with pytest.raises(user_settings.UserSettingsError):
        user_settings.load_user_settings(default_path)

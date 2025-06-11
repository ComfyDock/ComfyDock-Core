# test_user_settings.py

import pytest
from src.comfydock_core.user_settings import UserSettingsManager, UserSettings, UserSettingsError, UserSettingsNotFoundError, Folder

# To run: uv run pytest .\tests\test_user_settings.py

# Import the module we want to test.
from comfydock_core import user_settings

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

@pytest.fixture
def settings_manager(tmp_path):
    """Fixture to create a UserSettingsManager with temporary files"""
    settings_file = tmp_path / "user.settings.json"
    lock_file = tmp_path / "user.settings.json.lock"
    return UserSettingsManager(
        settings_file=str(settings_file),
        lock_file=str(lock_file),
        default_comfyui_path="/default/path"
    )

def test_load_nonexistent_settings(settings_manager):
    """Test that loading settings when no file exists raises UserSettingsNotFoundError"""
    with pytest.raises(UserSettingsNotFoundError):
        settings_manager.load()

def test_save_and_load_settings(settings_manager):
    """Test saving settings and loading them back"""
    settings = UserSettings(
        comfyui_path="/my/path",
        port="8000",
        runtime="cpu",
        command="run",
        folders=[Folder(id="folder1", name="Folder One")],
        max_deleted_environments=5
    )
    settings_manager.save(settings)
    loaded_settings = settings_manager.load()
    
    assert loaded_settings.comfyui_path == settings.comfyui_path
    assert loaded_settings.port == settings.port
    assert loaded_settings.runtime == settings.runtime
    assert loaded_settings.command == settings.command
    assert len(loaded_settings.folders) == 1
    assert loaded_settings.folders[0].id == "folder1"
    assert loaded_settings.folders[0].name == "Folder One"
    assert loaded_settings.max_deleted_environments == settings.max_deleted_environments

def test_update_settings(settings_manager):
    """Test updating settings with partial values"""
    # First create initial settings
    initial_settings = UserSettings(
        comfyui_path="/initial/path",
        port="8188",
        runtime="nvidia",
        command="",
        folders=[],
        max_deleted_environments=10
    )
    settings_manager.save(initial_settings)

    new_values = {
        "comfyui_path": "/updated/path",
        "port": "9000",
        "runtime": "cpu",
        "command": "start",
        "max_deleted_environments": 7
    }
    updated_settings = settings_manager.update(new_values)
    
    assert updated_settings.comfyui_path == "/updated/path"
    assert updated_settings.port == "9000"
    assert updated_settings.runtime == "cpu"
    assert updated_settings.command == "start"
    assert updated_settings.max_deleted_environments == 7

def test_corrupt_settings_file(settings_manager):
    """Test handling of corrupt settings file"""
    # Write invalid JSON to the settings file
    with open(settings_manager.settings_file, "w") as f:
        f.write("this is not valid JSON")
    
    with pytest.raises(UserSettingsError):
        settings_manager.load()

def test_create_folder(settings_manager):
    """Test creating a new folder"""
    # First create initial settings
    initial_settings = UserSettings(
        comfyui_path="/path",
        port="8188",
        runtime="nvidia",
        command="",
        folders=[],
        max_deleted_environments=10
    )
    settings_manager.save(initial_settings)
    
    settings = settings_manager.load()
    updated_settings = settings_manager.create_folder(settings, "New Folder")
    
    assert len(updated_settings.folders) == 1
    assert updated_settings.folders[0].name == "New Folder"
    assert updated_settings.folders[0].id is not None

def test_create_duplicate_folder(settings_manager):
    """Test creating a folder with duplicate name"""
    # First create initial settings
    initial_settings = UserSettings(
        comfyui_path="/path",
        port="8188",
        runtime="nvidia",
        command="",
        folders=[],
        max_deleted_environments=10
    )
    settings_manager.save(initial_settings)
    
    settings = settings_manager.load()
    settings_manager.create_folder(settings, "Test Folder")
    
    with pytest.raises(ValueError, match="Folder name must be unique"):
        settings_manager.create_folder(settings, "Test Folder")

def test_update_folder(settings_manager):
    """Test updating a folder name"""
    # First create initial settings
    initial_settings = UserSettings(
        comfyui_path="/path",
        port="8188",
        runtime="nvidia",
        command="",
        folders=[],
        max_deleted_environments=10
    )
    settings_manager.save(initial_settings)
    
    settings = settings_manager.load()
    settings = settings_manager.create_folder(settings, "Old Name")
    folder_id = settings.folders[0].id
    
    updated_settings = settings_manager.update_folder(settings, folder_id, "New Name")
    assert updated_settings.folders[0].name == "New Name"

def test_delete_folder(settings_manager):
    """Test deleting a folder"""
    # First create initial settings
    initial_settings = UserSettings(
        comfyui_path="/path",
        port="8188",
        runtime="nvidia",
        command="",
        folders=[],
        max_deleted_environments=10
    )
    settings_manager.save(initial_settings)
    
    settings = settings_manager.load()
    settings = settings_manager.create_folder(settings, "Test Folder")
    folder_id = settings.folders[0].id
    
    envs = []
    
    updated_settings = settings_manager.delete_folder(settings, folder_id, envs)
    assert len(updated_settings.folders) == 0

def test_delete_nonexistent_folder(settings_manager):
    """Test deleting a folder that doesn't exist"""
    # First create initial settings
    initial_settings = UserSettings(
        comfyui_path="/path",
        port="8188",
        runtime="nvidia",
        command="",
        folders=[],
        max_deleted_environments=10
    )
    settings_manager.save(initial_settings)
    
    settings = settings_manager.load()
    envs = []
    with pytest.raises(ValueError, match="Folder not found"):
        settings_manager.delete_folder(settings, "nonexistent-id", envs)

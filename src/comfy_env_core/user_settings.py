# user_settings.py

import json
from pathlib import Path
from filelock import FileLock, Timeout
from pydantic import BaseModel, ValidationError

USER_SETTINGS_FILE = "user.settings.json"
USER_SETTINGS_LOCK_FILE = f"{USER_SETTINGS_FILE}.lock"


class Folder(BaseModel):
    id: str
    name: str


class UserSettings(BaseModel):
    comfyui_path: str
    port: int = 8188
    runtime: str = "nvidia"
    command: str = ""
    folders: list[Folder] = []
    max_deleted_environments: int = 10


class UserSettingsError(Exception):
    """Custom exception type for user settings errors."""
    pass


def load_user_settings(default_comfyui_path: str) -> UserSettings:
    """
    Load user settings from the JSON file.
    If the file does not exist, returns default settings with the provided comfyui_path.

    Args:
        default_comfyui_path (str): The default path to use if settings file is missing.

    Returns:
        UserSettings: The loaded or default user settings.

    Raises:
        UserSettingsError: If there is an error acquiring the lock, parsing JSON, or validating data.
    """
    lock = FileLock(USER_SETTINGS_LOCK_FILE, timeout=10)
    try:
        with lock:
            if Path(USER_SETTINGS_FILE).exists():
                with open(USER_SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                    try:
                        return UserSettings(**data)
                    except ValidationError as e:
                        raise UserSettingsError(
                            f"Validation error in user settings: {e}"
                        )
            else:
                return UserSettings(comfyui_path=default_comfyui_path)
    except Timeout:
        raise UserSettingsError("Could not acquire file lock to load user settings.")
    except Exception as e:
        raise UserSettingsError(f"Error loading user settings: {e}")


def save_user_settings(settings: UserSettings):
    """
    Save user settings to the JSON file.

    Args:
        settings (UserSettings): The user settings to save.

    Raises:
        UserSettingsError: If there is an error acquiring the lock or writing the file.
    """
    lock = FileLock(USER_SETTINGS_LOCK_FILE, timeout=10)
    try:
        with lock:
            with open(USER_SETTINGS_FILE, "w") as f:
                json.dump(settings.model_dump(), f, indent=4)
    except Timeout:
        raise UserSettingsError("Could not acquire file lock to save user settings.")
    except Exception as e:
        raise UserSettingsError(f"Error saving user settings: {e}")


def update_user_settings(new_settings: dict) -> UserSettings:
    """
    Update user settings with new values.

    Args:
        new_settings (dict): A dictionary of values to update.

    Returns:
        UserSettings: The updated user settings.

    Raises:
        UserSettingsError: If an error occurs during update.
    """
    try:
        current_settings = load_user_settings(new_settings.get("comfyui_path", ""))
        updated_settings = current_settings.model_copy(update=new_settings)
        save_user_settings(updated_settings)
        return updated_settings
    except Exception as e:
        raise UserSettingsError(f"Error updating user settings: {e}")

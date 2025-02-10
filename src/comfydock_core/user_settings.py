# user_settings.py

import json
import uuid
from pathlib import Path
from filelock import FileLock, Timeout
from pydantic import BaseModel, ValidationError
from typing import Optional, List, Dict, Any

from .logging import get_logger

logger = get_logger(__name__)


class Folder(BaseModel):
    id: str
    name: str


class UserSettings(BaseModel):
    comfyui_path: str
    port: int = 8188
    runtime: str = "nvidia"
    command: str = ""
    folders: List[Folder] = []
    max_deleted_environments: int = 10


class UserSettingsError(Exception):
    """Custom exception type for user settings errors."""

    pass


class UserSettingsManager:
    def __init__(
        self,
        settings_file: str = "user.settings.json",
        lock_file: Optional[str] = None,
        lock_timeout: int = 10,
        default_comfyui_path: str = "ComfyUI",
    ):
        self.settings_file = Path(settings_file)
        self.lock_file = Path(lock_file or f"{settings_file}.lock")
        self.default_comfyui_path = default_comfyui_path
        self.lock_timeout = lock_timeout

    def _acquire_lock(self) -> FileLock:
        """Create and acquire a file lock with configured timeout."""
        return FileLock(self.lock_file, timeout=self.lock_timeout)

    def load(self) -> UserSettings:
        """
        Load user settings from the configured file.
        Creates default settings if file doesn't exist.
        """
        lock = self._acquire_lock()
        try:
            with lock:
                if self.settings_file.exists():
                    with open(self.settings_file, "r") as f:
                        data = json.load(f)
                        return UserSettings(**data)
                else:
                    return UserSettings(comfyui_path=self.default_comfyui_path)
        except Timeout:
            logger.error("Could not acquire file lock to load settings")
            raise UserSettingsError("Could not acquire file lock to load settings")
        except (ValidationError, json.JSONDecodeError) as e:
            logger.error("Invalid settings format: %s", e)
            raise UserSettingsError(f"Invalid settings format: {str(e)}")
        except Exception as e:
            logger.error("Error loading settings: %s", e)
            raise UserSettingsError(f"Error loading settings: {str(e)}")

    def save(self, settings: UserSettings) -> None:
        """Save user settings to the configured file."""
        lock = self._acquire_lock()
        try:
            with lock:
                with open(self.settings_file, "w") as f:
                    json.dump(settings.model_dump(), f, indent=4)
        except Timeout:
            logger.error("Could not acquire file lock to save settings")
            raise UserSettingsError("Could not acquire file lock to save settings")
        except Exception as e:
            logger.error("Error saving settings: %s", e)
            raise UserSettingsError(f"Error saving settings: {str(e)}")

    def update(self, new_settings: Dict[str, Any]) -> UserSettings:
        """Update settings with partial values and persist changes."""
        current = self.load()
        updated = current.model_copy(update=new_settings)
        self.save(updated)
        return updated

    def create_folder(self, settings: UserSettings, folder_name: str) -> UserSettings:
        """Create a new folder in the settings with validation."""
        if len(folder_name) > 128:
            logger.error("Folder name exceeds 128 characters limit")
            raise ValueError("Folder name exceeds 128 characters limit")

        if any(f.name == folder_name for f in settings.folders):
            logger.error("Folder name must be unique")
            raise ValueError("Folder name must be unique")

        new_folder = Folder(id=str(uuid.uuid4()), name=folder_name)
        settings.folders.append(new_folder)
        return settings

    def update_folder(
        self, settings: UserSettings, folder_id: str, new_name: str
    ) -> UserSettings:
        """Update an existing folder's name with validation."""
        folder = next((f for f in settings.folders if f.id == folder_id), None)
        if not folder:
            logger.error("Folder not found")
            raise ValueError("Folder not found")

        if len(new_name) > 128:
            logger.error("Folder name exceeds 128 characters limit")
            raise ValueError("Folder name exceeds 128 characters limit")

        if any(f.name == new_name and f.id != folder_id for f in settings.folders):
            logger.error("Folder name must be unique")
            raise ValueError("Folder name must be unique")

        folder.name = new_name
        return settings

    def delete_folder(self, settings: UserSettings, folder_id: str) -> UserSettings:
        """Delete a folder from settings if it exists."""
        original_count = len(settings.folders)
        settings.folders = [f for f in settings.folders if f.id != folder_id]

        if len(settings.folders) == original_count:
            logger.error("Folder not found")
            raise ValueError("Folder not found")

        return settings

    def validate_folder_usage(
        self,
        settings: UserSettings,
        folder_id: str,
        environment_manager: Any,  # Should use proper type hint for EnvironmentManager
    ) -> None:
        """Check if a folder is used by any environments."""
        envs = environment_manager.load_environments()
        if any(folder_id in env.folderIds for env in envs):
            logger.error("Folder contains environments and cannot be deleted")
            raise ValueError("Folder contains environments and cannot be deleted")

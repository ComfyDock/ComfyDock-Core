# environment_manager.py

import time
from .docker_interface import DockerInterface, DockerInterfaceContainerNotFoundError
from .persistence import (
    save_environments as persistence_save_environments,
    load_environments as persistence_load_environments,
    PersistenceError,
)

# Constants for file paths and defaults.
DB_FILE = "environments.json"
DEFAULT_LOCK_FILE = f"{DB_FILE}.lock"
DELETED_FOLDER_ID = "deleted"

SIGNAL_TIMEOUT = 2  # seconds

# Pydantic models for environments.
from pydantic import BaseModel


class Environment(BaseModel):
    name: str
    image: str
    container_name: str = ""
    id: str = ""
    status: str = ""
    command: str = ""
    comfyui_path: str = ""
    duplicate: bool = False
    options: dict = {}
    metadata: dict = {}
    folderIds: list[str] = []


class EnvironmentUpdate(BaseModel):
    name: str = None
    folderIds: list[str] = []


class EnvironmentManager:
    def __init__(self, db_file: str = DB_FILE, lock_file: str = DEFAULT_LOCK_FILE):
        self.db_file = db_file
        self.lock_file = lock_file
        # Create a docker interface
        self.docker_iface = DockerInterface()

    def _save_environments(self, environments: list) -> None:
        """Persist the environments list to the JSON file."""
        try:
            persistence_save_environments(environments, self.db_file, self.lock_file)
        except PersistenceError as e:
            raise Exception(f"Error saving environments: {str(e)}")


    def load_environments(self, folder_id: str = None) -> list:
        """
        Load environments from the JSON database, update each container's status,
        and optionally filter by folder_id.
        """
        environments = []
        try:
            environments = persistence_load_environments(self.db_file, self.lock_file)
        except PersistenceError as e:
            raise Exception(f"Error loading environments: {str(e)}")

        # Update container statuses.
        for env in environments:
            try:
                container = self.docker_iface.get_container(env["id"])
                env["status"] = container.status
            except DockerInterfaceContainerNotFoundError:
                env["status"] = "dead"
            except Exception as e:
                raise Exception(f"Error updating container status: {str(e)}")

        # Save updated statuses.
        self._save_environments(environments)

        # Optionally filter by folder_id.
        if folder_id:
            if folder_id == "all":
                environments = [
                    env
                    for env in environments
                    if DELETED_FOLDER_ID not in env.get("folderIds", [])
                ]
            else:
                environments = [
                    env for env in environments if folder_id in env.get("folderIds", [])
                ]
        return environments

    def check_environment_name(self, environments: list, env: Environment):
        """
        Validate the environment's name. For example, ensure that the name isn't too long.
        (You can also check for uniqueness here if desired.)
        """
        if len(env.name) > 128:
            raise Exception(
                "Environment name is too long. Maximum length is 128 characters."
            )
        # Uncomment if you want to enforce uniqueness:
        # if any(e["name"] == env.name for e in environments):
        #     raise Exception("Environment name already exists.")

    def create_environment(
        self, env: Environment, container_id: str, image: str
    ) -> Environment:
        """
        Create a new environment record by adding it to the database.
        """
        environments = self.load_environments()
        self.check_environment_name(environments, env)

        env_dict = env.dict()
        env_dict.update(
            {
                "image": image,
                "id": container_id,
                "duplicate": False,
                "status": "created",
                "metadata": {"base_image": image, "created_at": time.time()},
            }
        )

        environments.append(env_dict)
        self._save_environments(environments)
        return Environment(**env_dict)

    def update_environment(self, env_id: str, update: EnvironmentUpdate) -> Environment:
        """
        Update an existing environment record.
        """
        environments = self.load_environments()
        found = False
        for env in environments:
            if env["id"] == env_id:
                if update.name is not None:
                    env["name"] = update.name
                if update.folderIds is not None:
                    env["folderIds"] = update.folderIds
                found = True
                updated_env = env
                break
        if not found:
            raise Exception("Environment not found.")
        self._save_environments(environments)
        return Environment(**updated_env)

    def hard_delete_environment(
        self, env: dict, environments: list, timeout: int = SIGNAL_TIMEOUT
    ) -> None:
        """
        Permanently delete the environment: stop and remove its container, and if it’s a duplicate,
        remove its backing image.
        """
        try:
            container = self.docker_iface.get_container(env["id"])
            container.stop(timeout=timeout)
            container.remove()
        except DockerInterfaceContainerNotFoundError:
            pass
        except Exception as e:
            print(f"Error removing container {env['id']}: {str(e)}")

        if env.get("duplicate", False):
            try:
                self.docker_iface.remove_image(env["image"], force=True)
            except Exception as e:
                print(f"Error removing backing image {env['image']}: {str(e)}")
        environments.remove(env)

    def delete_environment(self, env_id: str, max_deleted: int = 10) -> dict:
        """
        Soft delete or hard delete an environment.
          - If the environment isn't marked as deleted, soft-delete it (by setting its folder to 'deleted').
          - If it’s already marked, then perform a hard deletion.
        """
        environments = self.load_environments()
        env = next((e for e in environments if e["id"] == env_id), None)
        if not env:
            raise Exception("Environment not found.")

        if DELETED_FOLDER_ID in env.get("folderIds", []):
            # Hard delete
            self.hard_delete_environment(env, environments)
            self._save_environments(environments)
            return {"status": "success (permanently deleted)", "id": env_id}
        else:
            # Soft delete: mark as deleted.
            env["folderIds"] = [DELETED_FOLDER_ID]
            env.setdefault("metadata", {})["deleted_at"] = time.time()
            self._save_environments(environments)
            self.prune_deleted_environments(max_deleted)
            return {"status": "success (moved to deleted folder)", "id": env_id}

    def prune_deleted_environments(self, max_deleted: int) -> None:
        """
        Remove the oldest soft-deleted environments if the total number exceeds the maximum allowed.
        """
        environments = self.load_environments()
        deleted_envs = [
            env for env in environments if DELETED_FOLDER_ID in env.get("folderIds", [])
        ]

        if len(deleted_envs) <= max_deleted:
            return

        # Sort deleted environments by deletion timestamp (oldest first).
        deleted_envs.sort(key=lambda e: e.get("metadata", {}).get("deleted_at", 0))
        to_remove = len(deleted_envs) - max_deleted

        for i in range(to_remove):
            self.hard_delete_environment(deleted_envs[i], environments)

        self._save_environments(environments)

    def duplicate_environment(
        self, env_id: str, new_env: Environment, container_commit_fn
    ) -> Environment:
        """
        Duplicate an environment by committing its container state to a new image and
        creating a new environment record. The `container_commit_fn` is an external function
        that should take a container and return a new image tag or identifier.
        """
        environments = self.load_environments()
        original = next((e for e in environments if e["id"] == env_id), None)
        if not original:
            raise Exception("Original environment not found.")

        if original.get("status") == "created":
            raise Exception("Environment can only be duplicated after activation.")

        container = self.docker_iface.get_container(env_id)
        image_repo = "comfy-env-clone"
        new_container_name = f"comfy-env-{new_env.name}-{int(time.time())}"
        unique_image = f"{image_repo}:{new_container_name}"

        try:
            # Commit the container to create a new image.
            new_image = container_commit_fn(container, image_repo, new_container_name)
        except Exception as e:
            raise Exception(f"Error committing container: {str(e)}")

        # Update the new environment details.
        new_env.container_name = new_container_name
        new_env_dict = new_env.dict()
        new_env_dict.update(
            {
                "image": unique_image,
                "id": new_container_name,  # This is a placeholder; update it with the real container ID after creation.
                "duplicate": True,
                "status": "created",
                "metadata": {
                    "created_at": time.time(),
                    "base_image": original["image"],
                },
            }
        )

        environments.append(new_env_dict)
        self._save_environments(environments)
        return Environment(**new_env_dict)

    def activate_environment(
        self, env_id: str, allow_multiple: bool = False, options: dict = None
    ) -> dict:
        """
        Activate an environment:
          - Stop other running containers (if allow_multiple is False).
          - Start this container if it is not already running.
          - (If the environment is in the "created" state, additional copy/setup steps
            can be performed externally.)
        """
        environments = self.load_environments()
        env = next((e for e in environments if e["id"] == env_id), None)
        if not env:
            raise Exception("Environment not found.")

        try:
            container = self.docker_iface.get_container(env["id"])
        except Exception:
            raise Exception("Container not found.")

        # Stop all other running containers if multiple activations aren’t allowed.
        if not allow_multiple:
            for other in environments:
                if other["id"] != env_id and other.get("status") == "running":
                    try:
                        other_container = self.docker_iface.get_container(other["id"])
                        other_container.stop(timeout=SIGNAL_TIMEOUT)
                    except Exception:
                        pass

        if container.status != "running":
            container.start()

        # (Optional: if in the "created" state, trigger copying of files into the container.)
        if env.get("status") == "created":
            # External logic can handle custom node installation or file copying.
            pass

        env["status"] = "running"
        self._save_environments(environments)
        return {"status": "success", "container_id": env_id}

    def deactivate_environment(self, env_id: str) -> dict:
        """
        Deactivate an environment by stopping its container.
        """
        environments = self.load_environments()
        env = next((e for e in environments if e["id"] == env_id), None)
        if not env:
            raise Exception("Environment not found.")

        try:
            container = self.docker_iface.get_container(env["id"])
        except Exception:
            raise Exception("Container not found.")

        if container.status in ["stopped", "exited", "created", "dead"]:
            return {"status": "success", "container_id": env_id}

        container.stop(timeout=SIGNAL_TIMEOUT)
        env["status"] = "stopped"
        self._save_environments(environments)
        return {"status": "success", "container_id": env_id}

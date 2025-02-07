# environment.py

import time
from pathlib import Path
from pydantic import BaseModel
from docker.types import DeviceRequest
from .docker_interface import DockerInterface, DockerInterfaceContainerNotFoundError
from .persistence import (
    save_environments as persistence_save_environments,
    load_environments as persistence_load_environments,
    PersistenceError,
)
from .utils import generate_id

# Constants for file paths and defaults.
DB_FILE = "environments.json"
DEFAULT_LOCK_FILE = f"{DB_FILE}.lock"
DELETED_FOLDER_ID = "deleted"
SIGNAL_TIMEOUT = 2  # seconds
COMFYUI_PORT = 8188


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

    def _save_environments(self, environments: list[Environment]) -> None:
        """Persist the environments list to the JSON file."""
        # Validate that environments is a list of Environment objects.
        if not all(isinstance(env, Environment) for env in environments):
            raise Exception("All environments must be Environment objects.")
        try:
            # Convert environments to dicts if they are Environment objects
            envs_list = [env.model_dump() for env in environments]

            persistence_save_environments(envs_list, self.db_file, self.lock_file)
        except PersistenceError as e:
            raise Exception(f"Error saving environments: {str(e)}")
        
    def _update_environment(self, env: Environment, environments: list[Environment]) -> None:
        """Update an environment in the list."""
        # Validate that environments is a list of Environment objects.
        if not all(isinstance(env, Environment) for env in environments):
            raise Exception("All environments must be Environment objects.")
        for i, e in enumerate(environments):
            if e.id == env.id:
                environments[i] = env

                break
        self._save_environments(environments)

    def _remove_environment(self, env: Environment, environments: list[Environment]) -> None:
        """Remove an environment from the list."""
        # Validate that environments is a list of Environment objects.
        if not all(isinstance(env, Environment) for env in environments):
            raise Exception("All environments must be Environment objects.")
        environments[:] = [e for e in environments if e.id != env.id]
        self._save_environments(environments)


    def get_environment(self, env_id: str) -> Environment:
        """
        Get an environment by its ID.
        """
        environments = self.load_environments()
        env = next((e for e in environments if e.id == env_id), None)
        if not env:
            raise Exception("Environment not found.")
        return env

    def load_environments(self, folder_id: str = None) -> list[Environment]:
        """
        Load environments from the JSON database, update each container's status,
        and optionally filter by folder_id.
        """
        try:
            environments = persistence_load_environments(self.db_file, self.lock_file)
        except PersistenceError as e:
            raise Exception(f"Error loading environments: {str(e)}")
        
        environments = [Environment(**env) for env in environments]

        # Update container statuses.
        for env in environments:
            try:
                container = self.docker_iface.get_container(env.id)
                env.status = container.status
            except DockerInterfaceContainerNotFoundError:
                env.status = "dead"
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
                    if DELETED_FOLDER_ID not in env.folderIds
                ]
            else:
                environments = [
                    env for env in environments if folder_id in env.folderIds
                ]
        return environments


    def check_environment_name(self, env: Environment, environments: list[Environment]):
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

    def create_environment(self, env: Environment) -> Environment:
        """
        Create a new environment record by adding it to the database.
        """
        environments = self.load_environments()
        self.check_environment_name(env, environments)

        # Convert comfyui_path to a Path object
        comfyui_path = Path(env.comfyui_path)

        # Ensure the image is available locally (or pull it)
        self.docker_iface.try_pull_image(env.image)

        # Create mounts from the provided mount_config (if any)
        mount_config = env.options.get("mount_config", {})
        mounts = self.docker_iface.create_mounts(mount_config, comfyui_path)
        # Determine port and update command accordingly
        port = env.options.get("port", COMFYUI_PORT)
        combined_cmd = f" --port {port} {env.command}"

        # Determine runtime and device requests
        runtime = "nvidia" if env.options.get("runtime", "") == "nvidia" else None
        device_requests = (
            [DeviceRequest(count=-1, capabilities=[["gpu"]])] if runtime else None
        )

        # Generate a unique container name (assumes you have a generate_id helper)
        container_name = f"comfy-env-{generate_id()}"
        env.container_name = container_name

        # Create the container via the docker interface
        container = self.docker_iface.create_container(
            image=env.image,
            name=container_name,
            command=combined_cmd,
            # runtime=runtime,
            device_requests=device_requests,
            ports={str(port): port},
            mounts=mounts,
        )

        # Set metadata and update environment record
        env.metadata = {"base_image": env.image, "created_at": time.time()}
        env.id = container.id
        env.status = "created"

        environments.append(env)
        self._save_environments(environments)
        return env

    def duplicate_environment(self, env_id: str, new_env: Environment) -> Environment:
        """
        Duplicate an existing environment by committing the original container's state
        to a new image and then creating a new container.
        """
        environments = self.load_environments()

        original = next((e for e in environments if e.id == env_id), None)
        if not original:
            raise Exception("Original environment not found.")
        if original.status == "created":
            raise Exception("Environment can only be duplicated after activation.")

        # Use the original comfyui_path to resolve mounts
        comfyui_path = Path(original.comfyui_path)

        mount_config = new_env.options.get("mount_config", {})
        mounts = self.docker_iface.create_mounts(mount_config, comfyui_path)

        port = new_env.options.get("port", COMFYUI_PORT)
        combined_cmd = f" --port {port} {new_env.command}"

        runtime = "nvidia" if new_env.options.get("runtime", "") == "nvidia" else None
        device_requests = (
            [DeviceRequest(count=-1, capabilities=[["gpu"]])] if runtime else None
        )

        container_name = f"comfy-env-{generate_id()}"
        new_env.container_name = container_name

        # Retrieve the original container and commit it to create a new image.
        original_container = self.docker_iface.get_container(env_id)
        image_repo = "comfy-env-clone"
        unique_image = f"{image_repo}:{container_name}"

        try:
            self.docker_iface.commit_container(
                original_container, image_repo, container_name
            )
        except Exception as e:
            raise Exception(f"Error committing container: {str(e)}")

        # Create a new container from the committed image
        new_container = self.docker_iface.create_container(
            image=unique_image,
            name=container_name,
            command=combined_cmd,
            # runtime=runtime,
            device_requests=device_requests,
            ports={str(port): port},
            mounts=mounts,
        )

        # Inherit and update metadata as needed
        new_env.metadata = original.metadata
        new_env.metadata["created_at"] = time.time()
        new_env.id = new_container.id
        new_env.image = unique_image
        new_env.status = "created"
        new_env.duplicate = True

        environments.append(new_env)
        self._save_environments(environments)
        return new_env

    def update_environment(self, env_id: str, update: EnvironmentUpdate) -> Environment:
        """
        Update an existing environment record.
        """
        environments = self.load_environments()
        env = self.get_environment(env_id)

        if update.name is not None:
            env.name = update.name
            if env.container_name is None:
                env.container_name = env.name
        if update.folderIds is not None:
            env.folderIds = update.folderIds

        updated_env = env

        self._save_environments(environments)
        return updated_env

    def activate_environment(
        self, env_id: str, allow_multiple: bool = False, options: dict = None
    ) -> Environment:
        """
        Activate an environment:
          - Stop other running containers (if allow_multiple is False).
          - Start this container if it is not already running.
          - (If the environment is in the "created" state, additional copy/setup steps
            can be performed externally.)
        """
        environments = self.load_environments()
        env = self.get_environment(env_id)

        container = self.docker_iface.get_container(env.id)

        # Stop all other running containers if multiple activations aren't allowed.
        if not allow_multiple:
            for other in environments:
                if other.id != env_id and other.status == "running":
                    try:
                        other_container = self.docker_iface.get_container(other.id)

                        self.docker_iface.stop_container(other_container)
                    except Exception:
                        pass

        # Start the container if it isn't running.
        self.docker_iface.start_container(container)

        # Get the container's comfyui path
        comfyui_path = Path(env.comfyui_path)

        # Get the container's mount config
        mount_config = env.options.get("mount_config", {})

        # (Optional: if in the "created" state, trigger copying of files into the container.)
        if env.status == "created":
            installed_nodes = self.docker_iface.copy_directories_to_container(
                env_id, comfyui_path, mount_config
            )

            if installed_nodes:
                self.docker_iface.restart_container(container)

        env.status = "running"
        self._save_environments(environments)
        return env

    def deactivate_environment(self, env_id: str) -> Environment:
        """
        Deactivate an environment by stopping its container.
        """
        environments = self.load_environments()
        env = self.get_environment(env_id)

        # Get the container
        container = self.docker_iface.get_container(env.id)

        # If the container is already stopped, return the environment
        if container.status in ["stopped", "exited", "created", "dead"]:
            return env

        self.docker_iface.stop_container(container)
        env.status = "stopped"
        self._save_environments(environments)
        return env

    def _hard_delete_environment(
        self, env: Environment, environments: list[Environment], timeout: int = SIGNAL_TIMEOUT
    ) -> None:
        """
        Permanently delete the environment: stop and remove its container, and if it's a duplicate,
        remove its backing image.
        """
        try:
            container = self.docker_iface.get_container(env.id)
            self.docker_iface.stop_container(container, timeout=timeout)
            self.docker_iface.remove_container(container)
        except DockerInterfaceContainerNotFoundError:
            print(f"Container {env.id} not found")
            pass
        except Exception as e:
            print(f"Error removing container {env.id}: {str(e)}")

        if env.duplicate:
            try:
                self.docker_iface.remove_image(env.image, force=True)
            except Exception as e:
                print(f"Error removing backing image {env.image}: {str(e)}")

        self._remove_environment(env, environments)

    def _prune_deleted_environments(self, environments: list[Environment], max_deleted: int) -> None:
        """
        Remove the oldest soft-deleted environments if the total number exceeds the maximum allowed.
        """
        deleted_envs = [
            env for env in environments if DELETED_FOLDER_ID in env.folderIds
        ]
        print(f"Deleted environments: {len(deleted_envs)}")
        if len(deleted_envs) <= max_deleted:
            return

        # Sort deleted environments by deletion timestamp (oldest first).
        deleted_envs.sort(key=lambda e: e.metadata.get("deleted_at", 0))
        to_remove = len(deleted_envs) - max_deleted
        print(f"To remove: {to_remove}")

        for i in range(to_remove):
            print(f"Removing environment: {deleted_envs[i]}")
            try:
                self._hard_delete_environment(deleted_envs[i], environments)
            except Exception as e:
                print(f"Error removing environment {deleted_envs[i]}: {str(e)}")
            print(f"Environments after hard delete: {environments}")


    def delete_environment(self, env_id: str, max_deleted: int = 10) -> str:
        """
        Soft delete or hard delete an environment.
          - If the environment isn't marked as deleted, soft-delete it (by setting its folder to 'deleted').

          - If it's already marked, then perform a hard deletion.
        """
        environments = self.load_environments()
        env = self.get_environment(env_id)

        if DELETED_FOLDER_ID in env.folderIds:
            print("Hard deleting environment")
            # Hard delete
            self._hard_delete_environment(env, environments)
            self._save_environments(environments)
            return env_id
        else:
            print("Soft deleting environment")
            # Soft delete: mark as deleted.
            env.folderIds = [DELETED_FOLDER_ID]
            env.metadata["deleted_at"] = time.time()
            print(f"Env: {env}")
            print(f"Environments before update: {environments}")
            self._update_environment(env, environments)
            print(f"Environments after update: {environments}")
            self._prune_deleted_environments(environments, max_deleted)
            print(f"Environments after prune: {environments}")
            self._save_environments(environments)
            print(f"Environments after save: {environments}")
            return env_id


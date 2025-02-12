# environment.py

import asyncio
import time
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel
from docker.types import DeviceRequest
from .docker_interface import DockerInterface, DockerInterfaceContainerNotFoundError
from .persistence import (
    save_environments as persistence_save_environments,
    load_environments as persistence_load_environments,
    PersistenceError,
)

from .utils import generate_id
import logging

logger = logging.getLogger(__name__)

# Constants
DB_FILE = "environments.json"
DEFAULT_LOCK_FILE = f"{DB_FILE}.lock"
DELETED_FOLDER_ID = "deleted"
SIGNAL_TIMEOUT = 0  # seconds
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
    folderIds: List[str] = []


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    folderIds: Optional[List[str]] = None


class EnvironmentManager:
    def __init__(self, db_file: str = DB_FILE, lock_file: str = DEFAULT_LOCK_FILE):
        self.db_file = db_file
        self.lock_file = lock_file
        self.docker_iface = DockerInterface()
        self.ws_manager = None
        logger.info(
            "Initialized EnvironmentManager with db_file: %s and lock_file: %s",
            self.db_file,
            self.lock_file,
        )

    def set_ws_manager(self, manager):
        self.ws_manager = manager

    async def notify_update(self):
        if self.ws_manager:
            logger.info("Notifying WebSocket manager")
            # environments = self.load_environments()
            await self.ws_manager.broadcast(
                {
                    "type": "environments_update",
                    # "data": [env.model_dump() for env in environments]
                }
            )

    async def monitor_docker_events(self):
        """Non-blocking Docker event monitoring"""
        logger.info("Starting Docker event monitoring")
        try:
            async for event in self.docker_iface.event_listener():
                logger.debug("Docker event: %s", event)
                if event.get("Type") == "container":
                    await self.notify_update()
        except asyncio.CancelledError:
            logger.info("Docker event monitoring stopped")
        except Exception as e:
            logger.error("Error in Docker event monitoring: %s", e)

    def _validate_environments_list(self, environments: List[Environment]) -> None:
        logger.debug(
            "Validating environments list with %d environments", len(environments)
        )

        if not all(isinstance(env, Environment) for env in environments):
            logger.error("Validation failed: Not all items are Environment instances")
            raise ValueError("All environments must be Environment instances.")

    def _save_environments(self, environments: List[Environment]) -> None:
        self._validate_environments_list(environments)
        try:
            envs_list = [env.model_dump() for env in environments]
            logger.info(
                "Saving %d environments to file %s", len(environments), self.db_file
            )
            persistence_save_environments(envs_list, self.db_file, self.lock_file)
            logger.debug("Successfully saved environments")
        except PersistenceError as e:
            logger.error("Error saving environments: %s", e)
            raise RuntimeError(f"Error saving environments: {e}")

    def _update_environment(
        self, new_env: Environment, environments: List[Environment]
    ) -> None:
        self._validate_environments_list(environments)
        logger.debug("Updating environment with id: %s", new_env.id)
        for i, env in enumerate(environments):
            if env.id == new_env.id:
                environments[i] = new_env
                logger.debug("Updated environment with id: %s", new_env.id)
                return
        logger.error("Environment with id %s not found in list", new_env.id)
        raise ValueError("Environment not found in list")

    def _remove_environment(
        self, env: Environment, environments: List[Environment]
    ) -> None:
        self._validate_environments_list(environments)
        logger.debug("Removing environment with id: %s", env.id)
        environments[:] = [e for e in environments if e.id != env.id]
        logger.debug("Environment with id %s removed", env.id)

    def _find_environment(
        self, env_id: str, environments: List[Environment]
    ) -> Environment:
        logger.debug("Searching for environment with id: %s", env_id)
        for env in environments:
            if env.id == env_id:
                logger.debug("Found environment with id: %s", env_id)
                return env
        logger.error("Environment with id %s not found", env_id)
        raise ValueError(f"Environment {env_id} not found")

    def get_environment(self, env_id: str) -> Environment:
        logger.info("Getting environment with id: %s", env_id)
        environments = self.load_environments()
        return self._find_environment(env_id, environments)

    def load_environments(self, folder_id: Optional[str] = None) -> List[Environment]:
        logger.info("Loading environments from file: %s", self.db_file)
        try:
            raw_envs = persistence_load_environments(self.db_file, self.lock_file)
            logger.debug("Loaded raw environments: %s", raw_envs)
        except PersistenceError as e:
            logger.error("Error loading environments: %s", e)
            raise RuntimeError(f"Error loading environments: {e}")

        environments = [Environment(**env) for env in raw_envs]
        logger.debug("Converted raw environments to Environment instances")

        for env in environments:
            # logger.debug("Updating container status for environment id: %s", env.id)
            try:
                container = self.docker_iface.get_container(env.id)
                env.status = container.status
                # logger.debug("Container status for environment %s: %s", env.id, container.status)
            except DockerInterfaceContainerNotFoundError:
                env.status = "dead"
                logger.warning(
                    "Container for environment %s not found, setting status to 'dead'",
                    env.id,
                )
            except Exception as e:
                logger.error(
                    "Error updating container status for environment %s: %s", env.id, e
                )
                raise RuntimeError(f"Error updating container status: {e}")

        self._save_environments(environments)
        logger.info("Environments saved after status update")

        if folder_id:
            logger.debug("Filtering environments by folder_id: %s", folder_id)
            filter_fn = lambda e: (
                DELETED_FOLDER_ID not in e.folderIds
                if folder_id == "all"
                else folder_id in e.folderIds
            )
            filtered_envs = [env for env in environments if filter_fn(env)]
            logger.debug(
                "Returning %d environments after filtering", len(filtered_envs)
            )
            return filtered_envs
        logger.info("Returning %d environments", len(environments))
        return environments

    def check_environment_name(
        self, env: Environment, environments: List[Environment]
    ) -> None:
        logger.debug("Checking environment name length for environment: %s", env.name)
        if len(env.name) > 128:
            logger.error("Environment name exceeds 128 characters: %s", env.name)
            raise ValueError("Environment name exceeds 128 characters")

    def _generate_container_name(self) -> str:
        container_name = f"comfy-env-{generate_id()}"
        logger.debug("Generated container name: %s", container_name)
        return container_name

    def _build_command(self, port: int, base_command: str) -> str:
        command = f"--port {port} {base_command}".strip()
        logger.debug("Built command: %s", command)
        return command

    def _get_device_requests(self, runtime: str) -> Optional[List[DeviceRequest]]:
        device_requests = (
            [DeviceRequest(count=-1, capabilities=[["gpu"]])]
            if runtime == "nvidia"
            else None
        )
        logger.debug("Device requests for runtime '%s': %s", runtime, device_requests)
        return device_requests

    def _create_container_config(self, env: Environment) -> tuple:
        logger.debug("Creating container configuration for environment id: %s", env.id)
        comfyui_path = Path(env.comfyui_path)
        mount_config = env.options.get("mount_config", {})
        mounts = self.docker_iface.create_mounts(mount_config, comfyui_path)

        port = env.options.get("port", COMFYUI_PORT)
        command = self._build_command(port, env.command)

        runtime = env.options.get("runtime", "")
        device_requests = self._get_device_requests(runtime)

        logger.debug(
            "Container config for environment %s: mounts=%s, port=%s, command='%s', device_requests=%s",
            env.id,
            mounts,
            port,
            command,
            device_requests,
        )
        return mounts, port, command, device_requests

    def create_environment(self, env: Environment) -> Environment:
        logger.info("Creating new environment: %s", env.name)
        environments = self.load_environments()
        self.check_environment_name(env, environments)

        # Check if image exists locally, if not throw an error
        logger.info("Checking if image exists locally: %s", env.image)
        img = self.docker_iface.get_image(env.image)
        if img is None:
            logger.error("Image %s not found locally", env.image)
            raise RuntimeError(f"Image {env.image} not found locally")

        mounts, port, command, device_requests = self._create_container_config(env)

        env.container_name = self._generate_container_name()
        logger.info("Creating container with name: %s", env.container_name)
        container = self.docker_iface.create_container(
            image=env.image,
            name=env.container_name,
            command=command,
            device_requests=device_requests,
            ports={str(port): port},
            mounts=mounts,
        )

        env.id = container.id
        env.status = "created"
        env.metadata = {"base_image": env.image, "created_at": time.time()}
        logger.info("Environment created with container id: %s", env.id)

        environments.append(env)
        self._save_environments(environments)
        logger.info("Environment %s saved", env.id)
        return env

    def duplicate_environment(self, env_id: str, new_env: Environment) -> Environment:
        logger.info(
            "Duplicating environment with original id: %s to new environment: %s",
            env_id,
            new_env.name,
        )
        environments = self.load_environments()
        original = self._find_environment(env_id, environments)

        if original.status == "created":
            logger.error(
                "Cannot duplicate environment %s because its status is 'created'",
                env_id,
            )
            raise RuntimeError("Environment can only be duplicated after activation")

        mounts, port, command, device_requests = self._create_container_config(new_env)
        new_env.container_name = self._generate_container_name()

        logger.info("Committing original container with id: %s", env_id)
        original_container = self.docker_iface.get_container(env_id)
        unique_image = f"comfy-env-clone:{new_env.container_name}"
        self.docker_iface.commit_container(
            original_container, "comfy-env-clone", new_env.container_name
        )

        logger.info(
            "Creating container for duplicated environment with name: %s",
            new_env.container_name,
        )
        self.docker_iface.create_container(
            image=unique_image,
            name=new_env.container_name,
            command=command,
            device_requests=device_requests,
            ports={str(port): port},
            mounts=mounts,
        )

        new_env.id = (
            new_env.container_name
        )  # Assuming container.id is set correctly in create_container
        new_env.image = unique_image
        new_env.status = "created"
        new_env.duplicate = True
        new_env.metadata = {**original.metadata, "created_at": time.time()}

        environments.append(new_env)
        self._save_environments(environments)
        logger.info("Duplicated environment created with id: %s", new_env.id)
        return new_env

    def update_environment(self, env_id: str, update: EnvironmentUpdate) -> Environment:
        logger.info("Updating environment with id: %s", env_id)
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)

        if update.name is not None:
            logger.debug("Updating name for environment %s to %s", env_id, update.name)
            env.name = update.name
            if not env.container_name:
                env.container_name = env.name
                logger.debug("Container name not set, using name: %s", env.name)

        if update.folderIds is not None:
            logger.debug(
                "Updating folderIds for environment %s to %s", env_id, update.folderIds
            )
            env.folderIds = update.folderIds

        self._update_environment(env, environments)
        self._save_environments(environments)
        logger.info("Environment %s updated successfully", env_id)
        return env

    def _stop_other_environments(
        self, current_env_id: str, environments: List[Environment]
    ) -> None:
        logger.info(
            "Stopping other running environments excluding id: %s", current_env_id
        )
        for env in environments:
            if env.id != current_env_id and env.status == "running":
                logger.debug("Stopping environment with id: %s", env.id)
                try:
                    container = self.docker_iface.get_container(env.id)
                    self.docker_iface.stop_container(container)
                    logger.info("Stopped environment with id: %s", env.id)
                except DockerInterfaceContainerNotFoundError:
                    logger.warning(
                        "Container for environment %s not found during stop operation",
                        env.id,
                    )
                    continue

    def activate_environment(
        self, env_id: str, allow_multiple: bool = False
    ) -> Environment:
        logger.info("Activating environment with id: %s", env_id)
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)
        container = self.docker_iface.get_container(env.id)

        if not allow_multiple:
            self._stop_other_environments(env_id, environments)

        logger.info("Starting container for environment %s", env.id)
        self.docker_iface.start_container(container)

        if env.status == "created":
            logger.info("Copying directories to container for environment %s", env.id)
            logger.debug("env: %s", env)
            comfyui_path = Path(env.comfyui_path)
            mount_config = env.options.get("mount_config", {})
            custom_nodes_installed = self.docker_iface.copy_directories_to_container(
                container.id, comfyui_path, mount_config
            )
            if custom_nodes_installed:
                logger.info("Custom nodes installed for environment %s", env.id)
                logger.info("Restarting container for environment %s", env.id)
                self.docker_iface.restart_container(container)

        env.status = "running"
        self._update_environment(env, environments)
        self._save_environments(environments)
        logger.info("Environment %s activated and running", env.id)
        return env

    def deactivate_environment(self, env_id: str) -> Environment:
        logger.info("Deactivating environment with id: %s", env_id)
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)
        container = self.docker_iface.get_container(env.id)

        if container.status not in ("stopped", "exited", "created", "dead"):
            logger.info("Stopping container for environment %s", env.id)
            self.docker_iface.stop_container(container, timeout=SIGNAL_TIMEOUT)
            env.status = "stopped"
            self._update_environment(env, environments)
            self._save_environments(environments)
            logger.info("Environment %s deactivated", env.id)
        return env

    def _hard_delete_environment(
        self, env: Environment, environments: List[Environment]
    ) -> None:
        logger.info("Hard deleting environment with id: %s", env.id)
        try:
            container = self.docker_iface.get_container(env.id)
            logger.debug(
                "Stopping container for environment %s with timeout %s",
                env.id,
                SIGNAL_TIMEOUT,
            )
            self.docker_iface.stop_container(container, timeout=SIGNAL_TIMEOUT)
            logger.debug("Removing container for environment %s", env.id)
            self.docker_iface.remove_container(container)
            logger.info("Container for environment %s removed", env.id)
        except DockerInterfaceContainerNotFoundError:
            logger.warning(
                "Container for environment %s not found during hard delete", env.id
            )

        if env.duplicate:
            try:
                logger.debug("Removing duplicate image for environment %s", env.id)
                self.docker_iface.remove_image(env.image, force=True)
                logger.info("Duplicate image %s removed", env.image)
            except Exception as e:
                logger.warning("Failed to remove duplicate image %s: %s", env.image, e)

        self._remove_environment(env, environments)
        logger.info("Environment %s removed from environment list", env.id)

    def _prune_deleted_environments(
        self, environments: List[Environment], max_deleted: int
    ) -> None:
        deleted_envs = [
            env for env in environments if DELETED_FOLDER_ID in env.folderIds
        ]
        logger.info(
            "Pruning deleted environments: found %d, max allowed %d",
            len(deleted_envs),
            max_deleted,
        )
        if len(deleted_envs) <= max_deleted:
            logger.debug("No pruning needed, count is within limit")
            return

        deleted_envs.sort(key=lambda e: e.metadata.get("deleted_at", 0))
        num_to_prune = len(deleted_envs) - max_deleted
        logger.info("Pruning %d environments", num_to_prune)
        for env in deleted_envs[:num_to_prune]:
            logger.debug("Hard deleting environment during prune: %s", env.id)
            self._hard_delete_environment(env, environments)

    def delete_environment(self, env_id: str, max_deleted: int = 10) -> str:
        logger.info("Deleting environment with id: %s", env_id)
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)

        if DELETED_FOLDER_ID in env.folderIds:
            logger.info(
                "Environment %s marked as deleted, proceeding with hard delete", env.id
            )
            self._hard_delete_environment(env, environments)
        else:
            logger.info("Marking environment %s as deleted", env.id)
            env.folderIds = [DELETED_FOLDER_ID]
            env.metadata["deleted_at"] = time.time()
            self._prune_deleted_environments(environments, max_deleted)

        self._save_environments(environments)
        logger.info("Environment %s deletion process completed", env_id)
        return env_id

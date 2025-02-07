# environment.py

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

# Constants
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
    folderIds: List[str] = []


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    folderIds: Optional[List[str]] = None


class EnvironmentManager:
    def __init__(self, db_file: str = DB_FILE, lock_file: str = DEFAULT_LOCK_FILE):
        self.db_file = db_file
        self.lock_file = lock_file
        self.docker_iface = DockerInterface()

    def _validate_environments_list(self, environments: List[Environment]) -> None:
        if not all(isinstance(env, Environment) for env in environments):
            raise ValueError("All environments must be Environment instances.")

    def _save_environments(self, environments: List[Environment]) -> None:
        self._validate_environments_list(environments)
        try:
            envs_list = [env.model_dump() for env in environments]
            persistence_save_environments(envs_list, self.db_file, self.lock_file)
        except PersistenceError as e:
            raise RuntimeError(f"Error saving environments: {e}")

    def _update_environment(self, new_env: Environment, environments: List[Environment]) -> None:
        self._validate_environments_list(environments)
        for i, env in enumerate(environments):
            if env.id == new_env.id:
                environments[i] = new_env
                return
        raise ValueError("Environment not found in list")

    def _remove_environment(self, env: Environment, environments: List[Environment]) -> None:
        self._validate_environments_list(environments)
        environments[:] = [e for e in environments if e.id != env.id]

    def _find_environment(self, env_id: str, environments: List[Environment]) -> Environment:
        for env in environments:
            if env.id == env_id:
                return env
        raise ValueError(f"Environment {env_id} not found")

    def get_environment(self, env_id: str) -> Environment:
        environments = self.load_environments()
        return self._find_environment(env_id, environments)

    def load_environments(self, folder_id: Optional[str] = None) -> List[Environment]:
        try:
            raw_envs = persistence_load_environments(self.db_file, self.lock_file)
        except PersistenceError as e:
            raise RuntimeError(f"Error loading environments: {e}")

        environments = [Environment(**env) for env in raw_envs]

        for env in environments:
            try:
                container = self.docker_iface.get_container(env.id)
                env.status = container.status
            except DockerInterfaceContainerNotFoundError:
                env.status = "dead"
            except Exception as e:
                raise RuntimeError(f"Error updating container status: {e}")

        self._save_environments(environments)

        if folder_id:
            filter_fn = (
                lambda e: DELETED_FOLDER_ID not in e.folderIds
                if folder_id == "all"
                else folder_id in e.folderIds
            )
            return [env for env in environments if filter_fn(env)]
        return environments

    def check_environment_name(self, env: Environment, environments: List[Environment]) -> None:
        if len(env.name) > 128:
            raise ValueError("Environment name exceeds 128 characters")

    def _generate_container_name(self) -> str:
        return f"comfy-env-{generate_id()}"

    def _build_command(self, port: int, base_command: str) -> str:
        return f"--port {port} {base_command}".strip()

    def _get_device_requests(self, runtime: str) -> Optional[List[DeviceRequest]]:
        return [DeviceRequest(count=-1, capabilities=[["gpu"]])] if runtime == "nvidia" else None

    def _create_container_config(self, env: Environment) -> tuple:
        comfyui_path = Path(env.comfyui_path)
        mount_config = env.options.get("mount_config", {})
        mounts = self.docker_iface.create_mounts(mount_config, comfyui_path)
        
        port = env.options.get("port", COMFYUI_PORT)
        command = self._build_command(port, env.command)
        
        runtime = env.options.get("runtime", "")
        device_requests = self._get_device_requests(runtime)
        
        return mounts, port, command, device_requests

    def create_environment(self, env: Environment) -> Environment:
        environments = self.load_environments()
        self.check_environment_name(env, environments)

        self.docker_iface.try_pull_image(env.image)
        mounts, port, command, device_requests = self._create_container_config(env)
        
        env.container_name = self._generate_container_name()
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
        
        environments.append(env)
        self._save_environments(environments)
        return env

    def duplicate_environment(self, env_id: str, new_env: Environment) -> Environment:
        environments = self.load_environments()
        original = self._find_environment(env_id, environments)

        if original.status == "created":
            raise RuntimeError("Environment can only be duplicated after activation")

        mounts, port, command, device_requests = self._create_container_config(new_env)
        new_env.container_name = self._generate_container_name()

        original_container = self.docker_iface.get_container(env_id)
        unique_image = f"comfy-env-clone:{new_env.container_name}"
        self.docker_iface.commit_container(original_container, "comfy-env-clone", new_env.container_name)

        self.docker_iface.create_container(
            image=unique_image,
            name=new_env.container_name,
            command=command,
            device_requests=device_requests,
            ports={str(port): port},
            mounts=mounts,
        )

        new_env.id = new_env.container_name  # Assuming container.id is set correctly in create_container
        new_env.image = unique_image
        new_env.status = "created"
        new_env.duplicate = True
        new_env.metadata = {**original.metadata, "created_at": time.time()}

        environments.append(new_env)
        self._save_environments(environments)
        return new_env

    def update_environment(self, env_id: str, update: EnvironmentUpdate) -> Environment:
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)

        if update.name is not None:
            env.name = update.name
            if not env.container_name:
                env.container_name = env.name
                
        if update.folderIds is not None:
            env.folderIds = update.folderIds

        self._update_environment(env, environments)
        self._save_environments(environments)
        return env

    def _stop_other_environments(self, current_env_id: str, environments: List[Environment]) -> None:
        for env in environments:
            if env.id != current_env_id and env.status == "running":
                try:
                    container = self.docker_iface.get_container(env.id)
                    self.docker_iface.stop_container(container)
                except DockerInterfaceContainerNotFoundError:
                    continue

    def activate_environment(self, env_id: str, allow_multiple: bool = False) -> Environment:
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)
        container = self.docker_iface.get_container(env.id)

        if not allow_multiple:
            self._stop_other_environments(env_id, environments)

        self.docker_iface.start_container(container)

        if env.status == "created":
            comfyui_path = Path(env.comfyui_path)
            mount_config = env.options.get("mount_config", {})
            self.docker_iface.copy_directories_to_container(env.id, comfyui_path, mount_config)
            self.docker_iface.restart_container(container)

        env.status = "running"
        self._update_environment(env, environments)
        self._save_environments(environments)
        return env

    def deactivate_environment(self, env_id: str) -> Environment:
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)
        container = self.docker_iface.get_container(env.id)

        if container.status not in ("stopped", "exited", "created", "dead"):
            self.docker_iface.stop_container(container)
            env.status = "stopped"
            self._update_environment(env, environments)
            self._save_environments(environments)
        return env

    def _hard_delete_environment(self, env: Environment, environments: List[Environment]) -> None:
        try:
            container = self.docker_iface.get_container(env.id)
            self.docker_iface.stop_container(container, timeout=SIGNAL_TIMEOUT)
            self.docker_iface.remove_container(container)
        except DockerInterfaceContainerNotFoundError:
            pass

        if env.duplicate:
            try:
                self.docker_iface.remove_image(env.image, force=True)
            except Exception:
                pass

        self._remove_environment(env, environments)

    def _prune_deleted_environments(self, environments: List[Environment], max_deleted: int) -> None:
        deleted_envs = [env for env in environments if DELETED_FOLDER_ID in env.folderIds]
        if len(deleted_envs) <= max_deleted:
            return

        deleted_envs.sort(key=lambda e: e.metadata.get("deleted_at", 0))
        for env in deleted_envs[: len(deleted_envs) - max_deleted]:
            self._hard_delete_environment(env, environments)

    def delete_environment(self, env_id: str, max_deleted: int = 10) -> str:
        environments = self.load_environments()
        env = self._find_environment(env_id, environments)

        if DELETED_FOLDER_ID in env.folderIds:
            self._hard_delete_environment(env, environments)
        else:
            env.folderIds = [DELETED_FOLDER_ID]
            env.metadata["deleted_at"] = time.time()
            self._prune_deleted_environments(environments, max_deleted)

        self._save_environments(environments)
        return env_id
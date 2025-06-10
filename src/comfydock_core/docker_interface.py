# docker_interface.py

import asyncio
from pathlib import Path
import platform
import posixpath
import tarfile
import docker
from docker.types import Mount, DeviceRequest
from docker.errors import APIError, NotFound
import tempfile
import re

from aiodocker import Docker
import logging

logger = logging.getLogger(__name__)

# Constants used by the interface
CONTAINER_COMFYUI_PATH = "/app/ComfyUI"
SIGNAL_TIMEOUT = 2
BLACKLIST_REQUIREMENTS = ["torch"]
EXCLUDE_CUSTOM_NODE_DIRS = ["__pycache__", "ComfyUI-Manager"]


class DockerInterfaceError(Exception):
    """
    Base class for Docker interface errors.
    """

    pass


class DockerInterfaceConnectionError(DockerInterfaceError):
    """
    Error raised when the Docker client fails to connect.
    """

    pass


class DockerInterfaceContainerNotFoundError(DockerInterfaceError):
    """
    Error raised when a container is not found.
    """

    pass


class DockerInterfaceImageNotFoundError(DockerInterfaceError):
    """
    Error raised when an image is not found.
    """

    pass


class DockerInterface:
    def __init__(self, timeout: int = 360):
        """
        Initialize the Docker client.
        """
        try:
            self.client = docker.from_env(timeout=timeout)
        except docker.errors.DockerException:
            raise DockerInterfaceConnectionError(
                "Failed to connect to Docker. Please ensure your Docker client is running."
            )

    async def event_listener(self):
        """Async generator for Docker events"""
        async with Docker() as docker:
            self._event_subscriber = docker.events.subscribe()
            try:
                while True:
                    event = await self._event_subscriber.get()
                    if event is None:
                        break
                    yield event
            except asyncio.CancelledError:
                logger.info("Event listening cancelled")

    def create_container(
        self,
        image: str,
        name: str | None = None,
        command: str | list[str] | None = None,
        entrypoint: str | list[str] | None = None,
        runtime: str | None = None,
        device_requests: list[DeviceRequest] | None = None,
        ports: dict[str, int | list[int] | tuple[str, int] | None] | None = None,
        mounts: list[Mount] | None = None,
        environment: dict[str, str] | list[str] | None = None,
        **kwargs,
    ):
        """
        Create a new container.
        """
        try:
            container = self.client.containers.create(
                image=image,
                name=name,
                command=command,
                entrypoint=entrypoint,
                runtime=runtime,
                device_requests=device_requests,
                ports=ports,
                mounts=mounts,
                environment=environment,
                **kwargs,
            )
            return container
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def get_container(self, container_id: str):
        """
        Retrieve a container by its ID.
        """
        try:
            return self.client.containers.get(container_id)
        except NotFound:
            raise DockerInterfaceContainerNotFoundError(
                f"Container {container_id} not found."
            )
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def commit_container(self, container, repository: str, tag: str):
        """
        Commit a container to create a new image.
        """
        try:
            return container.commit(repository=repository, tag=tag)
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def remove_image(self, image: str, force: bool = False):
        """
        Remove an image.
        """
        try:
            self.client.images.remove(image, force=force)
        except NotFound:
            pass
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def get_image(self, image: str):
        """
        Get an image.
        """
        try:
            return self.client.images.get(image)
        except NotFound:
            raise DockerInterfaceImageNotFoundError(f"Image {image} not found.")
        except APIError as e:
            raise DockerInterfaceError(str(e))
        
    def get_all_images(self):
        """
        Get all images, excluding dangling images.
        """
        try:
            # Filter out dangling images (those with <none>:<none> tag)
            return self.client.images.list(filters={"dangling": False})
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def start_container(self, container):
        """
        Start the container if it isn't running.
        """
        try:
            if container.status != "running":
                container.start()
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def stop_container(self, container, timeout: int = SIGNAL_TIMEOUT):
        """
        Stop the container.
        """
        try:
            container.stop(timeout=timeout)
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def remove_container(self, container):
        """
        Remove the container.
        """
        try:
            container.remove()
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def pull_image_api(self, image: str):
        """
        Pull an image via the Docker API, yielding the streaming output.
        """
        try:
            pull_stream = self.client.api.pull(image, stream=True, decode=True)
            for line in pull_stream:
                yield line
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def try_pull_image(self, image: str):
        """
        Check if an image exists locally; if not, pull it.
        """
        try:
            self.client.images.get(image)
            logger.info("Image %s found locally.", image)
        except docker.errors.ImageNotFound:
            logger.info("Image %s not found locally. Pulling from Docker Hub...", image)
            try:
                self.client.images.pull(image)
                logger.info("Image %s successfully pulled from Docker Hub.", image)
            except APIError as e:
                logger.error("Error pulling image %s: %s", image, e)
                raise DockerInterfaceError(str(e))
        except APIError as e:
            logger.error("Error pulling image %s: %s", image, e)
            raise DockerInterfaceError(str(e))

    def run_container(
        self,
        image: str,
        name: str,
        ports: dict,
        detach: bool = True,
        remove: bool = True,
        environment: dict = None,
    ):
        """
        Run a container from the given image with specified parameters.
        """
        try:
            container = self.client.containers.run(
                image, name=name, ports=ports, detach=detach, remove=remove, environment=environment
            )
            return container
        except APIError as e:
            logger.error("Error running container %s with image %s: %s", name, image, e)
            raise DockerInterfaceError(str(e))

    def ensure_directory_exists(self, container, path: str):
        """
        Ensure a directory exists inside a container.
        """
        try:
            container.exec_run(f"mkdir -p {path}")
        except APIError as e:
            logger.error("Error creating directory %s in container: %s", path, e)
            raise DockerInterfaceError(str(e))

    def copy_to_container(
        self,
        container_id: str,
        source_path: str,
        container_path: str,
        exclude_dirs: list = [],
    ):
        """
        Copy a directory or file from the host into a container.
        """
        try:
            container = self.get_container(container_id)
            self.ensure_directory_exists(container, container_path)
            with tempfile.TemporaryDirectory() as temp_dir:
                tar_path = Path(temp_dir) / "archive.tar"
                with tarfile.open(tar_path, mode="w") as archive:
                    for path in Path(source_path).rglob("*"):
                        if path.is_dir() and path.name in exclude_dirs:
                            continue
                        relative_path = path.relative_to(source_path)
                        archive.add(str(path), arcname=str(relative_path))
                with open(tar_path, "rb") as tar_data:
                    logger.info(
                        "Sending %s to %s:%s", source_path, container_id, container_path
                    )
                    try:
                        container.put_archive(container_path, tar_data)
                        logger.info(
                            "Copied %s to %s:%s",
                            source_path,
                            container_id,
                            container_path,
                        )
                    except Exception as e:
                        logger.error(
                            "Error sending %s to %s:%s: %s",
                            source_path,
                            container_id,
                            container_path,
                            e,
                        )
                        raise
        except docker.errors.NotFound:
            logger.error("Container %s not found.", container_id)
            raise DockerInterfaceContainerNotFoundError(
                f"Container {container_id} not found."
            )
        except APIError as e:
            logger.error("Docker API error: %s", e)
            raise DockerInterfaceError(str(e))
        except Exception as e:
            logger.error("An unexpected error occurred: %s", e)
            raise DockerInterfaceError(str(e))

    def convert_old_to_new_style(self, old_config: dict, comfyui_path: Path) -> dict:
        """
        Convert an old-style mount configuration into the new format.
        """
        new_config = {"mounts": []}
        for key, action in old_config.items():
            if action not in ["mount", "copy"]:
                continue
            host_subdir = (comfyui_path / key).resolve()
            container_subdir = Path(CONTAINER_COMFYUI_PATH) / key
            mount_entry = {
                "container_path": container_subdir.as_posix(),
                "host_path": host_subdir.as_posix(),
                "type": "mount",
                "read_only": False,
            }
            new_config["mounts"].append(mount_entry)
        return new_config

    def _process_copy_mount(
        self, mount: dict, comfyui_path: Path, container_id: str
    ) -> bool:
        """
        Process a mount entry with type 'copy'.
        """
        host_path_str = mount.get("host_path")
        container_path = mount.get("container_path")
        if not host_path_str or not container_path:
            logger.info(
                "Skipping mount entry because host_path or container_path is missing: %s",
                mount,
            )
            return False
        source_path = Path(host_path_str)
        if not source_path.is_absolute():
            source_path = (comfyui_path / source_path).resolve()
        if source_path.exists():
            logger.info("Copying %s to container at %s", source_path, container_path)
            self.copy_to_container(
                container_id, str(source_path), container_path, EXCLUDE_CUSTOM_NODE_DIRS
            )
            if "custom_nodes" in container_path:
                self.install_custom_nodes(
                    container_id, BLACKLIST_REQUIREMENTS, EXCLUDE_CUSTOM_NODE_DIRS
                )
                return True
        else:
            logger.info("Local path does not exist: %s", source_path)
        return False

    def _process_mount_mount(
        self, mount: dict, comfyui_path: Path, container_id: str
    ) -> bool:
        """
        For backward compatibility: if a mount entry of type 'mount' points to custom_nodes,
        run the custom nodes installation.
        """
        if mount.get("type") == "mount" and "custom_nodes" in mount.get(
            "container_path", ""
        ):
            self.install_custom_nodes(
                container_id, BLACKLIST_REQUIREMENTS, EXCLUDE_CUSTOM_NODE_DIRS
            )
            return True
        return False

    def copy_directories_to_container(
        self, container_id: str, comfyui_path: Path, mount_config: dict
    ) -> bool:
        """
        Copy specified directories from the host to the container based on the mount configuration.
        Supports both new-style (with a "mounts" list) and old-style configurations.
        Returns True if custom nodes were installed.
        """
        installed_custom_nodes = False
        logger.info("copy_directories_to_container: mount_config: %s", mount_config)
        if "mounts" in mount_config and isinstance(mount_config["mounts"], list):
            config = mount_config
        else:
            logger.info("Detected old style mount config. Converting to new style.")
            config = self.convert_old_to_new_style(mount_config, comfyui_path)
        logger.info("Using mount config: %s", config)
        for mount in config.get("mounts", []):
            action = mount.get("type", "").lower()
            if action == "copy":
                if self._process_copy_mount(mount, comfyui_path, container_id):
                    if "custom_nodes" in mount.get("container_path", ""):
                        installed_custom_nodes = True
            elif action == "mount":
                if self._process_mount_mount(mount, comfyui_path, container_id):
                    installed_custom_nodes = True
        return installed_custom_nodes

    def install_custom_nodes(
        self, container_id: str, blacklist: list = [], exclude_dirs: list = []
    ):
        """
        Install custom nodes by checking for requirements.txt files within the custom_nodes directory
        and running pip install for non-blacklisted dependencies.
        """
        container_custom_nodes_path = CONTAINER_COMFYUI_PATH + "/custom_nodes"
        container = self.get_container(container_id)
        exclude_conditions = " ".join(
            f"-not -name '{dir_name}'" for dir_name in exclude_dirs
        )
        exec_command = f"sh -c 'find {container_custom_nodes_path} -mindepth 1 -maxdepth 1 -type d {exclude_conditions}'"
        exec_id = container.exec_run(
            exec_command, stdout=True, stderr=True, stream=True
        )
        output = []
        logger.info("Listing directories in custom_nodes path:")
        for line in exec_id.output:
            decoded_line = line.decode("utf-8").strip()
            logger.info(decoded_line)
            output.append(decoded_line)
        output = "\n".join(output).split("\n") if output else []
        logger.info(output)
        for custom_node in output:
            logger.info("Checking %s", custom_node)
            requirements_path = posixpath.join(
                container_custom_nodes_path, custom_node, "requirements.txt"
            )
            check_command = (
                f"sh -c '[ -f {requirements_path} ] && echo exists || echo not_exists'"
            )
            check_exec_id = container.exec_run(check_command, stdout=True, stderr=True)
            if check_exec_id.output.decode("utf-8").strip() == "exists":
                logger.info(
                    "Found requirements.txt in %s, checking for blacklisted dependencies...",
                    custom_node,
                )
                read_command = f"sh -c 'cat {requirements_path}'"
                read_exec_id = container.exec_run(
                    read_command, stdout=True, stderr=True
                )
                requirements_content = (
                    read_exec_id.output.decode("utf-8").strip().split("\n")
                )
                filtered_requirements = []
                for line in requirements_content:
                    match = re.match(r"^\s*([a-zA-Z0-9\-_]+)", line)
                    if match:
                        package_name = match.group(1)
                        if package_name in blacklist:
                            logger.info("Skipping blacklisted dependency: %s", line)
                            continue
                    filtered_requirements.append(line)
                if filtered_requirements:
                    temp_requirements_path = posixpath.join(
                        container_custom_nodes_path,
                        custom_node,
                        "temp_requirements.txt",
                    )
                    create_temp_command = f"sh -c 'echo \"{chr(10).join(filtered_requirements)}\" > {temp_requirements_path}'"
                    container.exec_run(create_temp_command, stdout=True, stderr=True)
                    logger.info(
                        "Installing non-blacklisted dependencies for %s...", custom_node
                    )
                    install_command = f"sh -c 'pip install -r {temp_requirements_path}'"
                    install_exec_id = container.exec_run(
                        install_command, stdout=True, stderr=True, stream=True
                    )
                    for line in install_exec_id.output:
                        logger.info(line.decode("utf-8").strip())
                    remove_temp_command = f"sh -c 'rm {temp_requirements_path}'"
                    container.exec_run(remove_temp_command, stdout=True, stderr=True)
            else:
                logger.info("No requirements.txt found in %s.", custom_node)

    def restart_container(self, container_id: str):
        """
        Restart the container.
        """
        container = self.get_container(container_id)
        try:
            container.restart(timeout=SIGNAL_TIMEOUT)
        except APIError as e:
            raise DockerInterfaceError(str(e))

    def _create_mounts_from_new_config(self, mount_config: dict, comfyui_path: Path):
        """
        Create Docker mount bindings from a new-style mount configuration.
        """
        logger.info("Creating mounts for environment")
        mounts = []
        user_mounts = mount_config.get("mounts", [])
        for m in user_mounts:
            logger.info("Mount: %s", m)
            action = m.get("type", "").lower()
            if action not in ["mount", "copy"]:
                logger.info(
                    "Skipping mount for %s because type is '%s' (not 'mount' or 'copy').",
                    m,
                    action,
                )
                continue
            container_path = m.get("container_path")
            host_path = m.get("host_path")
            if not container_path or not host_path:
                logger.info(
                    "Skipping entry %s because container_path or host_path is missing.",
                    m,
                )
                continue
            source_path = Path(host_path)
            logger.info("source_path: %s", source_path)
            if not source_path.is_absolute():
                source_path = comfyui_path / source_path
                logger.info("source_path: %s", source_path)
            if not source_path.exists():
                logger.info(
                    "Host directory does not exist: %s. Creating directory.",
                    source_path,
                )
                source_path.mkdir(parents=True, exist_ok=True)
            source_str = str(source_path.resolve())
            logger.info("source_str: %s", source_str)
            target_str = str(Path(container_path).as_posix())
            logger.info("target_str: %s", target_str)
            read_only = m.get("read_only", False)
            logger.info(
                "Mounting host '%s' to container '%s' (read_only=%s)",
                source_str,
                target_str,
                read_only,
            )
            mounts.append(
                Mount(
                    target=target_str,
                    source=source_str,
                    type="bind",
                    read_only=read_only,
                )
            )

        # Check if on windows
        if platform.system() == "Windows":
            logger.info("Adding /usr/lib/wsl mount")
            mounts.append(
                Mount(
                    target="/usr/lib/wsl",
                    source="/usr/lib/wsl",
                    type="bind",
                    read_only=True,
                )
            )
        return mounts

    def create_mounts(self, mount_config: dict, comfyui_path: Path):
        """
        Main function to create mounts. Supports both new-style and old-style configurations.
        """
        config = mount_config
        if "mounts" not in config or not isinstance(config["mounts"], list):
            logger.info("Detected old style mount config. Converting to new style.")
            config = self.convert_old_to_new_style(mount_config, comfyui_path)
        return self._create_mounts_from_new_config(config, comfyui_path)

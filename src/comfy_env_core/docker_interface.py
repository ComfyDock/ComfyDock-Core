# docker_interface.py

from pathlib import Path
import posixpath
import tarfile
import docker
from docker.types import Mount
from docker.errors import APIError, NotFound
import tempfile
import re

# Constants used by the interface
CONTAINER_COMFYUI_PATH = "/app/ComfyUI"
SIGNAL_TIMEOUT = 2
BLACKLIST_REQUIREMENTS = ['torch']
EXCLUDE_CUSTOM_NODE_DIRS = ['__pycache__', 'ComfyUI-Manager']

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


    def create_container(self, image: str, name: str, command: str, runtime=None,
                        device_requests=None, ports: dict = None, mounts=None):
        """
        Create a new container.
        """
        try:
            container = self.client.containers.create(
                image=image,
                name=name,
                command=command,
                runtime=runtime,
                device_requests=device_requests,
                ports=ports,
                mounts=mounts,
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
            raise DockerInterfaceContainerNotFoundError(f"Container {container_id} not found.")
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


    def start_container(self, container):
        """
        Start the container if it isn’t running.
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
            print(f"Image {image} found locally.")
        except docker.errors.ImageNotFound:
            print(f"Image {image} not found locally. Pulling from Docker Hub...")
            try:
                self.client.images.pull(image)
                print(f"Image {image} successfully pulled from Docker Hub.")
            except APIError as e:
                print(f"Error pulling image {image}: {e}")
                raise DockerInterfaceError(str(e))
        except APIError as e:
            print(f"Error pulling image {image}: {e}")
            raise DockerInterfaceError(str(e))

    def run_container(self, image: str, name: str, ports: dict, detach: bool = True, remove: bool = True):
        """
        Run a container from the given image with specified parameters.
        """
        try:
            container = self.client.containers.run(
                image,
                name=name,
                ports=ports,
                detach=detach,
                remove=remove
            )
            return container
        except APIError as e:
            print(f"Error running container {name} with image {image}: {e}")
            raise DockerInterfaceError(str(e))

    def ensure_directory_exists(self, container, path: str):
        """
        Ensure a directory exists inside a container.
        """
        try:
            container.exec_run(f"mkdir -p {path}")
        except APIError as e:
            print(f"Error creating directory {path} in container: {e}")
            raise DockerInterfaceError(str(e))

    def copy_to_container(self, container_id: str, source_path: str, container_path: str, exclude_dirs: list = []):
        """
        Copy a directory or file from the host into a container.
        """
        try:
            container = self.get_container(container_id)
            self.ensure_directory_exists(container, container_path)
            with tempfile.TemporaryDirectory() as temp_dir:
                tar_path = Path(temp_dir) / "archive.tar"
                with tarfile.open(tar_path, mode="w") as archive:
                    for path in Path(source_path).rglob('*'):
                        if path.is_dir() and path.name in exclude_dirs:
                            continue
                        relative_path = path.relative_to(source_path)
                        archive.add(str(path), arcname=str(relative_path))
                with open(tar_path, "rb") as tar_data:
                    print(f"Sending {source_path} to {container_id}:{container_path}")
                    try:
                        container.put_archive(container_path, tar_data)
                        print(f"Copied {source_path} to {container_id}:{container_path}")
                    except Exception as e:
                        print(f"Error sending {source_path} to {container_id}:{container_path}: {e}")
                        raise
        except docker.errors.NotFound:
            print(f"Container {container_id} not found.")
            raise DockerInterfaceContainerNotFoundError(f"Container {container_id} not found.")
        except APIError as e:
            print(f"Docker API error: {e}")
            raise DockerInterfaceError(str(e))
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
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
                "read_only": False
            }
            new_config["mounts"].append(mount_entry)
        return new_config

    def _process_copy_mount(self, mount: dict, comfyui_path: Path, container_id: str) -> bool:
        """
        Process a mount entry with type 'copy'.
        """
        host_path_str = mount.get("host_path")
        container_path = mount.get("container_path")
        if not host_path_str or not container_path:
            print(f"Skipping mount entry because host_path or container_path is missing: {mount}")
            return False
        source_path = Path(host_path_str)
        if not source_path.is_absolute():
            source_path = (comfyui_path / source_path).resolve()
        if source_path.exists():
            print(f"Copying {source_path} to container at {container_path}")
            self.copy_to_container(container_id, str(source_path), container_path, EXCLUDE_CUSTOM_NODE_DIRS)
            if "custom_nodes" in container_path:
                self.install_custom_nodes(container_id, BLACKLIST_REQUIREMENTS, EXCLUDE_CUSTOM_NODE_DIRS)
                return True
        else:
            print(f"Local path does not exist: {source_path}")
        return False

    def _process_mount_mount(self, mount: dict, comfyui_path: Path, container_id: str) -> bool:
        """
        For backward compatibility: if a mount entry of type 'mount' points to custom_nodes,
        run the custom nodes installation.
        """
        if mount.get("type") == "mount" and "custom_nodes" in mount.get("container_path", ""):
            self.install_custom_nodes(container_id, BLACKLIST_REQUIREMENTS, EXCLUDE_CUSTOM_NODE_DIRS)
            return True
        return False

    def copy_directories_to_container(self, container_id: str, comfyui_path: Path, mount_config: dict) -> bool:
        """
        Copy specified directories from the host to the container based on the mount configuration.
        Supports both new-style (with a "mounts" list) and old-style configurations.
        Returns True if custom nodes were installed.
        """
        installed_custom_nodes = False
        print(f'copy_directories_to_container: mount_config: {mount_config}')
        if "mounts" in mount_config and isinstance(mount_config["mounts"], list):
            config = mount_config
        else:
            print("Detected old style mount config. Converting to new style.")
            config = self.convert_old_to_new_style(mount_config, comfyui_path)
        print(f"Using mount config: {config}")
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

    def install_custom_nodes(self, container_id: str, blacklist: list = [], exclude_dirs: list = []):
        """
        Install custom nodes by checking for requirements.txt files within the custom_nodes directory
        and running pip install for non-blacklisted dependencies.
        """
        container_custom_nodes_path = CONTAINER_COMFYUI_PATH + "/custom_nodes"
        container = self.get_container(container_id)
        exclude_conditions = ' '.join(f"-not -name '{dir_name}'" for dir_name in exclude_dirs)
        exec_command = f"sh -c 'find {container_custom_nodes_path} -mindepth 1 -maxdepth 1 -type d {exclude_conditions}'"
        exec_id = container.exec_run(exec_command, stdout=True, stderr=True, stream=True)
        output = []
        print("Listing directories in custom_nodes path:")
        for line in exec_id.output:
            decoded_line = line.decode('utf-8').strip()
            print(decoded_line)
            output.append(decoded_line)
        output = '\n'.join(output).split('\n') if output else []
        print(output)
        for custom_node in output:
            print(f"Checking {custom_node}")
            requirements_path = posixpath.join(container_custom_nodes_path, custom_node, "requirements.txt")
            check_command = f"sh -c '[ -f {requirements_path} ] && echo exists || echo not_exists'"
            check_exec_id = container.exec_run(check_command, stdout=True, stderr=True)
            if check_exec_id.output.decode('utf-8').strip() == "exists":
                print(f"Found requirements.txt in {custom_node}, checking for blacklisted dependencies...")
                read_command = f"sh -c 'cat {requirements_path}'"
                read_exec_id = container.exec_run(read_command, stdout=True, stderr=True)
                requirements_content = read_exec_id.output.decode('utf-8').strip().split('\n')
                filtered_requirements = []
                for line in requirements_content:
                    match = re.match(r'^\s*([a-zA-Z0-9\-_]+)', line)
                    if match:
                        package_name = match.group(1)
                        if package_name in blacklist:
                            print(f"Skipping blacklisted dependency: {line}")
                            continue
                    filtered_requirements.append(line)
                if filtered_requirements:
                    temp_requirements_path = posixpath.join(container_custom_nodes_path, custom_node, "temp_requirements.txt")
                    create_temp_command = f"sh -c 'echo \"{chr(10).join(filtered_requirements)}\" > {temp_requirements_path}'"
                    container.exec_run(create_temp_command, stdout=True, stderr=True)
                    print(f"Installing non-blacklisted dependencies for {custom_node}...")
                    install_command = f"sh -c 'pip install -r {temp_requirements_path}'"
                    install_exec_id = container.exec_run(install_command, stdout=True, stderr=True, stream=True)
                    for line in install_exec_id.output:
                        print(line.decode('utf-8').strip())
                    remove_temp_command = f"sh -c 'rm {temp_requirements_path}'"
                    container.exec_run(remove_temp_command, stdout=True, stderr=True)
            else:
                print(f"No requirements.txt found in {custom_node}.")

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
        print(f"Creating mounts for environment")
        mounts = []
        user_mounts = mount_config.get("mounts", [])
        for m in user_mounts:
            print(f"Mount: {m}")
            action = m.get("type", "").lower()
            if action not in ["mount", "copy"]:
                print(f"Skipping mount for {m} because type is '{action}' (not 'mount' or 'copy').")
                continue
            container_path = m.get("container_path")
            host_path = m.get("host_path")
            if not container_path or not host_path:
                print(f"Skipping entry {m} because container_path or host_path is missing.")
                continue
            source_path = Path(host_path)
            print(f"source_path: {source_path}")
            if not source_path.is_absolute():
                source_path = comfyui_path / source_path
                print(f"source_path: {source_path}")
            if not source_path.exists():
                print(f"Host directory does not exist: {source_path}. Creating directory.")
                source_path.mkdir(parents=True, exist_ok=True)
            source_str = str(source_path.resolve())
            print(f"source_str: {source_str}")
            target_str = str(Path(container_path).as_posix())
            print(f"target_str: {target_str}")
            read_only = m.get("read_only", False)
            print(f"Mounting host '{source_str}' to container '{target_str}' (read_only={read_only})")
            mounts.append(
                Mount(
                    target=target_str,
                    source=source_str,
                    type='bind',
                    read_only=read_only
                )
            )
        # Optionally add the /usr/lib/wsl mount if it exists.
        wsl_path = Path("/usr/lib/wsl")
        if wsl_path.exists():
            mounts.append(
                Mount(
                    target="/usr/lib/wsl",
                    source=str(wsl_path),
                    type='bind',
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
            print("Detected old style mount config. Converting to new style.")
            config = self.convert_old_to_new_style(mount_config, comfyui_path)
        return self._create_mounts_from_new_config(config, comfyui_path)

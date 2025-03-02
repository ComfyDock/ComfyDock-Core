# test_docker_interface.py

import logging
import pytest
from pathlib import Path

import docker.errors

from src.comfydock_core.docker_interface import (
    DockerInterface,
    DockerInterfaceConnectionError,
    DockerInterfaceContainerNotFoundError,
    DockerInterfaceImageNotFoundError,
)


# --- Updated Dummy Classes to Simulate Docker Behavior ---

class DummyExecResult:
    def __init__(self, output, stream=False):
        # Normalize output to a list of bytes.
        if isinstance(output, bytes):
            output = [output]
        elif isinstance(output, list):
            output = [line if isinstance(line, bytes) else str(line).encode("utf-8") for line in output]
        else:
            output = [str(output).encode("utf-8")]
        if stream:
            self.output = output
        else:
            # When not streaming, return a single bytes object.
            self.output = output[0] if output else b""

class DummyContainer:
    def __init__(self, id, status="stopped"):
        self.id = id
        self.status = status

    def start(self):
        self.status = "running"

    def stop(self, timeout=2):
        self.status = "stopped"

    def remove(self):
        pass

    def commit(self, repository, tag):
        return DummyImage(repository, tag)

    def exec_run(self, command, stdout=True, stderr=True, stream=False):
        # Simulate different commands based on the input.
        if command.startswith("mkdir -p"):
            return DummyExecResult(b"", stream=stream)
        if "find" in command:
            # When stream=True, return a list of bytes.
            return DummyExecResult([b"node1\n"], stream=True)
        if "[ -f" in command:
            return DummyExecResult(b"exists", stream=stream)
        if "cat" in command:
            return DummyExecResult(b"package1\npackage2", stream=stream)
        if "pip install" in command:
            return DummyExecResult([b"installed\n"], stream=True)
        return DummyExecResult(b"dummy", stream=stream)

    def restart(self, timeout):
        self.status = "running"

    def put_archive(self, container_path, tar_data):
        # Record that put_archive was called.
        self._put_archive_called = True
        return True

class DummyImage:
    def __init__(self, repository, tag):
        self.repository = repository
        self.tag = tag

class DummyContainersManager:
    def create(self, **kwargs):
        return DummyContainer(kwargs.get("name", "dummy_container"))
    def get(self, container_id):
        if container_id == "not_found":
            raise docker.errors.NotFound("Container not found")
        return DummyContainer(container_id)
    def run(self, image, name, ports, detach=True, remove=True, environment=None):
        return DummyContainer(name, status="running")

class DummyImagesManager:
    def get(self, image):
        if image == "not_found":
            raise docker.errors.NotFound("Image not found")
        return DummyImage("dummy_repo", "dummy_tag")
    def remove(self, image, force=False):
        if image == "error":
            raise docker.errors.APIError("Error removing image")
    def pull(self, image):
        if image == "error":
            raise docker.errors.APIError("Error pulling image")
        return DummyImage("dummy_repo", "dummy_tag")

class DummyAPI:
    def pull(self, image, stream, decode):
        yield {"status": "Downloading", "id": "dummy_layer"}

class DummyDockerClient:
    def __init__(self):
        self.containers = DummyContainersManager()
        self.images = DummyImagesManager()
        self.api = DummyAPI()

# --- Pytest Fixtures ---

@pytest.fixture
def docker_iface(monkeypatch):
    """
    Fixture to create a DockerInterface instance using a dummy Docker client.
    """
    def fake_from_env(*args, **kwargs):
        return DummyDockerClient()
    monkeypatch.setattr("src.comfydock_core.docker_interface.docker.from_env", fake_from_env)
    return DockerInterface()

@pytest.fixture(autouse=True)
def setup_logging():
    """Fixture to setup logging before each test"""
    # Create logger
    logger = logging.getLogger('src.comfydock_core.docker_interface')
    # Set level
    logger.setLevel(logging.INFO)
    # Create handler
    handler = logging.StreamHandler()
    # Create formatter
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    # Add handler to logger
    logger.addHandler(handler)
    yield
    # Clean up
    logger.handlers = []

# --- Tests for Initialization ---

def test_init_success(monkeypatch):
    def fake_from_env(*args, **kwargs):
        return DummyDockerClient()
    monkeypatch.setattr("src.comfydock_core.docker_interface.docker.from_env", fake_from_env)
    iface = DockerInterface()
    assert hasattr(iface, "client")

def test_init_connection_error(monkeypatch):
    monkeypatch.setattr(
        "src.comfydock_core.docker_interface.docker.from_env",
        lambda *args, **kwargs: (_ for _ in ()).throw(docker.errors.DockerException("error"))
    )
    with pytest.raises(DockerInterfaceConnectionError):
        DockerInterface()

# --- Tests for Container Methods ---

def test_create_container(docker_iface):
    container = docker_iface.create_container(
        image="dummy_image", name="test_container", command="echo hello"
    )
    assert isinstance(container, DummyContainer)
    assert container.id == "test_container"

def test_get_container_success(docker_iface):
    container = docker_iface.get_container("existing")
    assert isinstance(container, DummyContainer)
    assert container.id == "existing"

def test_get_container_not_found(docker_iface):
    with pytest.raises(DockerInterfaceContainerNotFoundError):
        docker_iface.get_container("not_found")

def test_commit_container(docker_iface):
    container = DummyContainer("test")
    image = docker_iface.commit_container(container, repository="repo", tag="v1")
    assert isinstance(image, DummyImage)
    assert image.repository == "repo"
    assert image.tag == "v1"

def test_remove_image_no_error(docker_iface):
    try:
        docker_iface.remove_image("not_found")
    except Exception:
        pytest.fail("remove_image raised an exception for not found image")

def test_get_image_success(docker_iface):
    image = docker_iface.get_image("dummy_image")
    assert isinstance(image, DummyImage)

def test_get_image_not_found(docker_iface):
    with pytest.raises(DockerInterfaceImageNotFoundError):
        docker_iface.get_image("not_found")

def test_start_container(docker_iface):
    container = DummyContainer("test", status="stopped")
    docker_iface.start_container(container)
    assert container.status == "running"

def test_stop_container(docker_iface):
    container = DummyContainer("test", status="running")
    docker_iface.stop_container(container, timeout=1)
    assert container.status == "stopped"

def test_remove_container(docker_iface):
    container = DummyContainer("test")
    docker_iface.remove_container(container)

def test_restart_container(docker_iface, monkeypatch):
    container = DummyContainer("test", status="stopped")
    monkeypatch.setattr(docker_iface, "get_container", lambda cid: container)
    docker_iface.restart_container("test")
    assert container.status == "running"

# --- Tests for Image Pulling and Running ---

def test_pull_image_api(docker_iface):
    gen = docker_iface.pull_image_api("dummy_image")
    first = next(gen)
    assert "status" in first

def test_try_pull_image_image_exists(docker_iface, caplog):
    caplog.set_level(logging.INFO)
    docker_iface.try_pull_image("dummy_image")
    print(caplog.text)
    assert "found locally" in caplog.text


def test_try_pull_image_pulls(docker_iface, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    def fake_get(image):
        raise docker.errors.ImageNotFound("Not found")
    monkeypatch.setattr(docker_iface.client.images, "get", fake_get)
    docker_iface.try_pull_image("dummy_image")
    assert "Pulling from Docker Hub" in caplog.text


def test_run_container(docker_iface):
    container = docker_iface.run_container(
        image="dummy_image", name="test_run", ports={"8000/tcp": 8000}
    )
    assert isinstance(container, DummyContainer)
    assert container.id == "test_run"

# --- Tests for Directory and Archive Operations ---

def test_ensure_directory_exists(docker_iface):
    container = DummyContainer("test")
    # Simply call and ensure no exception.
    docker_iface.ensure_directory_exists(container, "/some/path")

def test_copy_to_container(tmp_path, docker_iface, monkeypatch):
    # Create a temporary source directory with a file.
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    file_path = src_dir / "test.txt"
    file_path.write_text("hello world")
    # Create a dummy container that records if put_archive is called.
    class RecordingContainer(DummyContainer):
        def __init__(self, id, status="stopped"):
            super().__init__(id, status)
            self.archive_called = False
        def put_archive(self, container_path, tar_data):
            self.archive_called = True
            return True
    dummy_container = RecordingContainer("rec")
    monkeypatch.setattr(docker_iface, "get_container", lambda cid: dummy_container)
    docker_iface.copy_to_container("rec", str(src_dir), "/container/path", [])
    assert dummy_container.archive_called

# --- Tests for Mount Configuration ---

def test_convert_old_to_new_style(docker_iface):
    comfyui_path = Path("/comfy")
    old_config = {"user": "mount", "models": "mount", "invalid": "other"}
    new_config = docker_iface.convert_old_to_new_style(old_config, comfyui_path)
    assert "mounts" in new_config
    # Should have 2 mounts.
    assert len(new_config["mounts"]) == 2
    for mount in new_config["mounts"]:
        assert "container_path" in mount
        assert "host_path" in mount

def test_create_mounts_from_new_config(docker_iface, tmp_path):
    mount_config = {
        "mounts": [
            {
                "container_path": "/app/ComfyUI/models",
                "host_path": str(tmp_path / "models"),
                "type": "mount",
                "read_only": False,
            }
        ]
    }
    host_path = Path(mount_config["mounts"][0]["host_path"])
    if host_path.exists():
        for p in host_path.iterdir():
            p.unlink()
        host_path.rmdir()
    mounts = docker_iface._create_mounts_from_new_config(mount_config, tmp_path)
    assert isinstance(mounts, list)
    assert len(mounts) >= 1
    m = mounts[0]
    # Check the mount target; try attribute then dict-style.
    target = getattr(m, "target", None) or m.get("Target") if isinstance(m, dict) else None
    # If target is still None, check the object's __dict__.
    if target is None and hasattr(m, "__dict__"):
        target = m.__dict__.get("target")
    assert target == "/app/ComfyUI/models"

def test_create_mounts_old_style(docker_iface, tmp_path):
    old_config = {"models": "mount", "user": "copy"}
    mounts = docker_iface.create_mounts(old_config, tmp_path)
    assert isinstance(mounts, list)
    assert len(mounts) >= 2

# --- Test for install_custom_nodes ---

def test_install_custom_nodes(docker_iface, monkeypatch):
    # Create a dummy container that simulates exec_run for various commands.
    class CustomNodesContainer(DummyContainer):
        def exec_run(self, command, stdout=True, stderr=True, stream=False):
            if "find" in command:
                return DummyExecResult([b"node1\n"], stream=True)
            if "[ -f" in command:
                return DummyExecResult(b"exists", stream=False)
            if "cat" in command:
                return DummyExecResult(b"package1\npackage2", stream=False)
            if "pip install" in command:
                return DummyExecResult([b"installed\n"], stream=True)
            return DummyExecResult(b"", stream=stream)
    dummy_container = CustomNodesContainer("cn")
    monkeypatch.setattr(docker_iface, "get_container", lambda cid: dummy_container)
    # This should run without raising an error.
    docker_iface.install_custom_nodes("cn", blacklist=[], exclude_dirs=[])

# --- End of Tests ---

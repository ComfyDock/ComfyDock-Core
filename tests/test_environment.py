import time
import pytest

# Import the EnvironmentManager and related classes from your module.
# Adjust the import if your module structure differs.
from src.comfydock_core.environment import (
    Environment,
    EnvironmentManager,
    EnvironmentUpdate,
    DELETED_FOLDER_ID,
)
from src.comfydock_core.docker_interface import DockerInterfaceContainerNotFoundError


# --- Fake Docker Interface and Container ---

class FakeContainer:
    def __init__(self, container_id, status="created"):
        self.id = container_id
        self.status = status

    def start(self):
        self.status = "running"

    def stop(self, timeout=2):
        self.status = "stopped"

    def remove(self):
        self.status = "removed"


class FakeDockerInterface:
    def __init__(self):
        self.containers = {}
        self.next_id = 1

    def create_container(self, image, name, command, device_requests=None, ports=None, mounts=None, runtime=None, entrypoint=None, environment=None):
        container_id = f"container_{self.next_id}"
        self.next_id += 1
        container = FakeContainer(container_id, status="created")
        self.containers[container_id] = container
        return container

    def get_container(self, container_id):
        if container_id in self.containers:
            return self.containers[container_id]
        raise DockerInterfaceContainerNotFoundError(f"Container {container_id} not found.")

    def commit_container(self, container, repository, tag):
        # Simulate successful commit (no-op)
        return

    def try_pull_image(self, image):
        # Assume the image always exists (no-op)
        return

    def create_mounts(self, mount_config, comfyui_path):
        # Return an empty mounts list for simplicity.
        return []
    
    def get_image(self, image):
        return image

    def start_container(self, container):
        container.start()


    def stop_container(self, container, timeout=2):
        container.stop(timeout)

    def remove_container(self, container):
        if container.id in self.containers:
            del self.containers[container.id]

    def restart_container(self, container):
        container.status = "running"

    def copy_directories_to_container(self, container_id, comfyui_path, mount_config):
        # For testing, assume nothing is copied.
        return False

    def remove_image(self, image, force=False):
        # Assume image removal always works (no-op)
        return

# --- Fake Persistence Layer ---

@pytest.fixture(autouse=True)
def fake_persistence(monkeypatch):
    """
    Override the persistence functions to use an in-memory list.
    """
    fake_db = []

    def fake_save(environments, db_file, lock_file):
        # Overwrite the fake database contents.
        fake_db.clear()
        fake_db.extend(environments)

    def fake_load(db_file, lock_file):
        return fake_db.copy()

    # Patch the persistence functions in the environment module.
    import src.comfydock_core.environment
    monkeypatch.setattr(src.comfydock_core.environment, "persistence_save_environments", fake_save)
    monkeypatch.setattr(src.comfydock_core.environment, "persistence_load_environments", fake_load)
    return fake_db


# --- Manager Fixture ---

@pytest.fixture
def manager(monkeypatch, fake_persistence):
    """
    Return an EnvironmentManager instance with the fake Docker interface
    and fake persistence.
    """
    mgr = EnvironmentManager(db_file="dummy_db.json", lock_file="dummy_lock.lock")
    mgr.docker_iface = FakeDockerInterface()

    # Patch generate_id to always return a fixed value ("fixedid")
    import src.comfydock_core.environment
    monkeypatch.setattr(src.comfydock_core.environment, "generate_id", lambda: "fixedid")
    return mgr



# --- Tests ---

def test_create_environment(manager, fake_persistence):
    env = Environment(
        name="TestEnv",
        image="testimage",
        command="run",
        comfyui_path="/tmp",
        options={"port": 8000},
    )
    created_env = manager.create_environment(env)
    # Check that the container name uses the fixed generate_id value.
    assert created_env.container_name == "comfy-env-fixedid"
    # Our fake Docker interface creates container IDs like "container_1", etc.
    assert created_env.id.startswith("container_")
    assert created_env.status == "created"
    # Check that persistence now holds the new environment.
    environments = manager.load_environments()
    assert any(e.id == created_env.id for e in environments)

def test_update_environment(manager, fake_persistence):
    env = Environment(
        name="TestEnv",
        image="testimage",
        command="run",
        comfyui_path="/tmp"
    )
    created_env = manager.create_environment(env)
    update = EnvironmentUpdate(name="UpdatedEnv", folderIds=["folder1"])
    updated_env = manager.update_environment(created_env.id, update)
    assert updated_env.name == "UpdatedEnv"
    assert updated_env.folderIds == ["folder1"]

def test_activate_deactivate_environment(manager, fake_persistence):
    env = Environment(
        name="TestEnv",
        image="testimage",
        command="run",
        comfyui_path="/tmp",
        options={"port": 8000}
    )
    created_env = manager.create_environment(env)
    container = manager.docker_iface.get_container(created_env.id)
    # Initially, container status should be "created".
    assert container.status == "created"

    # Activate the environment.
    activated_env = manager.activate_environment(created_env.id)
    assert activated_env.status == "running"
    container = manager.docker_iface.get_container(created_env.id)
    assert container.status == "running"

    # Deactivate the environment.
    deactivated_env = manager.deactivate_environment(created_env.id)
    assert deactivated_env.status == "stopped"
    container = manager.docker_iface.get_container(created_env.id)
    assert container.status == "stopped"

def test_duplicate_environment(manager, fake_persistence):
    # Create an original environment.
    original_env = Environment(
        name="OriginalEnv",
        image="testimage",
        command="run",
        comfyui_path="/tmp",
        options={"port": 8000}
    )
    created_env = manager.create_environment(original_env)
    # To allow duplication, update the original's status to something other than "created".
    created_env.status = "running"
    # Update persistence manually.
    all_envs = manager.load_environments()
    for e in all_envs:
        if e.id == created_env.id:
            e.status = "running"
    manager._save_environments(all_envs)

    # Duplicate the environment.
    new_env = Environment(
        name="DuplicateEnv",
        image="testimage",
        command="run",
        comfyui_path="/tmp",
        options={"port": 8000}
    )
    with pytest.raises(Exception, match="Environment can only be duplicated after activation"):
        duplicate_env = manager.duplicate_environment(created_env.id, new_env)
    manager.activate_environment(created_env.id)
    duplicate_env = manager.duplicate_environment(created_env.id, new_env)
    assert duplicate_env.duplicate is True
    # The container name should use the fixed generate_id.
    assert duplicate_env.container_name == "comfy-env-fixedid"
    # The new image should be based on the clone naming.
    assert duplicate_env.image.startswith("comfy-env-clone")

    assert duplicate_env.status == "created"

def test_delete_environment(manager, fake_persistence):
    # Create an environment.
    env = Environment(
        name="TestEnv",
        image="testimage",
        command="run",
        comfyui_path="/tmp"
    )
    created_env = manager.create_environment(env)
    env_id = created_env.id

    # Initially, the environment should not be marked as deleted.
    assert DELETED_FOLDER_ID not in created_env.folderIds

    # Soft-delete the environment.
    returned_id = manager.delete_environment(env_id)
    assert returned_id == env_id
    updated_env = manager.get_environment(env_id)
    assert DELETED_FOLDER_ID in updated_env.folderIds

    # Hard-delete: calling delete_environment again should remove the environment.
    manager.delete_environment(env_id)
    with pytest.raises(Exception, match=f"Environment {env_id} not found"):
        manager.get_environment(env_id)

def test_prune_deleted_environments(manager, fake_persistence):
    """
    Create four environments, soft-delete them with max_deleted set to 3,
    and verify that the oldest environment is pruned (hard deleted)
    while only three soft-deleted environments remain.
    """
    envs = []
    for i in range(4):
        env = Environment(
            name=f"Env{i}",
            image="testimage",
            command="run",
            comfyui_path="/tmp",
            options={"port": 8000}
        )
        created = manager.create_environment(env)
        envs.append(created)
        # Sleep briefly to ensure distinct deletion timestamps.
        time.sleep(0.01)

    # Soft-delete each environment with max_deleted set to 3.
    for env in envs:
        manager.delete_environment(env.id, max_deleted=3)
        time.sleep(0.01)

    # The first environment (oldest) should have been hard-deleted.
    with pytest.raises(Exception, match=f"Environment {envs[0].id} not found"):
        manager.get_environment(envs[0].id)

    # The remaining environments should still be present and marked as deleted.
    for e in envs[1:]:
        env_obj = manager.get_environment(e.id)
        assert DELETED_FOLDER_ID in env_obj.folderIds
# test_comfyui_integration.py

import pytest
from pathlib import Path
import subprocess

from src.comfydock_core.comfyui_integration import (
    is_comfyui_repo,
    check_comfyui_path,
    try_install_comfyui,
    ComfyUIError,
    COMFYUI_DIRECTORY_NAME,
)

# --- Tests for is_comfyui_repo ---

def test_is_comfyui_repo_valid(tmp_path):
    # Create a valid ComfyUI repository structure.
    repo = tmp_path / "valid_repo"
    repo.mkdir()
    # Create required file.
    (repo / "main.py").write_text("print('hello')")
    # Create required directories.
    for d in ["models", "comfy", "comfy_execution", "web"]:
        (repo / d).mkdir()
    assert is_comfyui_repo(str(repo)) is True

def test_is_comfyui_repo_invalid_missing_comfy_core_dir_and_git_config(tmp_path):
    # Create a repo structure missing main.py.
    repo = tmp_path / "invalid_repo"
    repo.mkdir()
    for d in ["models"]:
        (repo / d).mkdir()
    assert is_comfyui_repo(str(repo)) is False

# --- Tests for check_comfyui_path ---

def test_check_comfyui_path_nonexistent(tmp_path):
    non_existent = tmp_path / "nonexistent"
    with pytest.raises(ComfyUIError) as excinfo:
        check_comfyui_path(str(non_existent))
    assert "does not exist" in str(excinfo.value)

def test_check_comfyui_path_not_directory(tmp_path):
    file_path = tmp_path / "somefile.txt"
    file_path.write_text("hello")
    with pytest.raises(ComfyUIError) as excinfo:
        check_comfyui_path(str(file_path))
    assert "not a directory" in str(excinfo.value)

def test_check_comfyui_path_invalid_repo(tmp_path):
    # Create a directory that doesn't have a valid ComfyUI repo.
    invalid_repo = tmp_path / "invalid_repo"
    invalid_repo.mkdir()
    with pytest.raises(ComfyUIError) as excinfo:
        check_comfyui_path(str(invalid_repo))
    assert "No valid ComfyUI installation found" in str(excinfo.value)

def test_check_comfyui_path_valid(tmp_path):
    # Create a valid repo structure.
    repo = tmp_path / "valid_repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')")
    for d in ["models", "comfy", "comfy_execution", "web"]:
        (repo / d).mkdir()
    result = check_comfyui_path(str(repo))
    assert result == repo

# --- Tests for try_install_comfyui ---

def test_try_install_comfyui_already_installed(tmp_path):
    """
    If a valid installation already exists, try_install_comfyui should simply return that path.
    Note: Since check_comfyui_path examines the provided path, we pass the actual ComfyUI directory.
    """
    base = tmp_path / "base"
    base.mkdir()
    comfyui_dir = base / COMFYUI_DIRECTORY_NAME
    comfyui_dir.mkdir()
    (comfyui_dir / "main.py").write_text("print('hello')")
    for d in ["models", "comfy", "comfy_execution", "web"]:
        (comfyui_dir / d).mkdir()
    # Pass the valid installation directory.
    result = try_install_comfyui(str(comfyui_dir))
    assert result == str(comfyui_dir)

def test_try_install_comfyui_install(monkeypatch, tmp_path):
    """
    If a valid installation does not exist at the base path, try_install_comfyui
    should attempt to clone the repository.
    We simulate a successful clone by monkeypatching subprocess.run and check_comfyui_path.
    """
    base = tmp_path / "base2"
    base.mkdir()
    # There is no valid installation at base.
    # Monkeypatch subprocess.run to simulate a successful git clone.
    def fake_run(cmd, check, capture_output, text):
        class DummyCompletedProcess:
            def __init__(self):
                self.stdout = "cloned successfully"
        return DummyCompletedProcess()
    monkeypatch.setattr(subprocess, "run", fake_run)
    # Also, override check_comfyui_path so that after "cloning" it returns the expected path.
    def fake_check(path: str):
        # Assume that after cloning, the valid installation is at path/ComfyUI.
        return Path(path) / COMFYUI_DIRECTORY_NAME
    monkeypatch.setattr("src.comfydock_core.comfyui_integration.check_comfyui_path", fake_check)
    result = try_install_comfyui(str(base))
    expected = str(base / COMFYUI_DIRECTORY_NAME)
    assert result == expected

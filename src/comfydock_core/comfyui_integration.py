# comfyui_integration.py

from pathlib import Path
import subprocess
import logging

logger = logging.getLogger(__name__)

# Repository configuration
COMFYUI_REPO_URL = "https://github.com/comfyanonymous/ComfyUI.git"
COMFYUI_DEFAULT_BRANCH = "master"

COMFYUI_DIRECTORY_NAME = "ComfyUI"


class ComfyUIError(Exception):
    """Custom exception for ComfyUI integration errors."""

    pass


def is_comfyui_repo(path: str) -> bool:
    """
    Check if the provided path points to a directory that appears
    to be a valid ComfyUI repository.
    
    Uses multiple methods to verify, starting with Git config
    and falling back to essential core files.
    """
    repo_path = Path(path)
    
    # Ensure the path is a directory
    if not repo_path.is_dir():
        logger.warning("ComfyUI path is not a directory: %s.", path)
        return False
    
    # Method 1: Check if it's a git repo with ComfyUI remote
    git_config_path = repo_path / ".git" / "config"
    if git_config_path.is_file():
        try:
            with open(git_config_path, "r") as f:
                config_content = f.read()
                if "comfyanonymous/ComfyUI" in config_content:
                    logger.info("Found ComfyUI remote in git config: %s", git_config_path)
                    return True
        except (IOError, UnicodeDecodeError):
            logger.warning("Failed to read git config: %s", git_config_path)
            pass  # Fall back to other methods if git config check fails
    
    # Method 2: Check for core directories
    core_dirs = ["comfy", "models"]
    if all((repo_path / dir_name).is_dir() for dir_name in core_dirs):
        logger.info("Found core directories: %s", core_dirs)
        return True
    
    logger.warning("No valid ComfyUI installation found at %s", repo_path)
    return False


def check_comfyui_path(path: str) -> Path:
    """
    Verify that the given path exists, is a directory, and contains a valid
    ComfyUI installation. Returns a Path object if valid, or raises a ComfyUIError.
    """
    comfyui_path = Path(path)

    if not comfyui_path.exists():
        logger.error("ComfyUI path does not exist: %s.", path)
        raise ComfyUIError(f"ComfyUI path does not exist: {path}.")

    if not comfyui_path.is_dir():
        logger.error("ComfyUI path is not a directory: %s.", path)
        raise ComfyUIError(f"ComfyUI path is not a directory: {path}.")

    if not is_comfyui_repo(path):
        logger.error("No valid ComfyUI installation found.")
        raise ComfyUIError("No valid ComfyUI installation found.")

    return comfyui_path


def try_install_comfyui(path: str, branch: str = COMFYUI_DEFAULT_BRANCH) -> str:
    """
    Attempt to ensure that a valid ComfyUI installation exists at the specified path.


    If check_comfyui_path fails with "No valid ComfyUI installation found.", this function
    will attempt to clone the repository from GitHub into a subdirectory called 'ComfyUI'.

    Args:
        path (str): The base path where ComfyUI should be installed.
        branch (str): The branch to clone from. Defaults to "master".

    Returns:
        str: The path to the ComfyUI directory.

    Raises:
        ComfyUIError: If installation fails or if the path is invalid for reasons other than
                    a missing installation.
    """
    try:
        # Check if a valid installation already exists.
        check_comfyui_path(path)
    except ComfyUIError as e:
        if str(e) != "No valid ComfyUI installation found.":
            raise
        logger.info("Installing ComfyUI from %s with branch %s", path, branch)
        comfyui_path = Path(path)
        try:
            comfyui_dir = comfyui_path / COMFYUI_DIRECTORY_NAME
            comfyui_dir.mkdir(parents=True, exist_ok=True)

            # Run the git clone command to clone the repository.
            clone_command = [
                "git",
                "clone",
                "--branch",
                branch,
                COMFYUI_REPO_URL,
                str(comfyui_dir),
            ]
            result = subprocess.run(
                clone_command, check=True, capture_output=True, text=True
            )
            logger.info(result.stdout)
            return str(comfyui_dir)
        except subprocess.CalledProcessError as e:
            raise ComfyUIError(
                f"Failed to clone ComfyUI repository to {comfyui_dir}. Error: {e.stderr}"
            )
        except Exception as e:
            raise ComfyUIError(f"Error during ComfyUI installation: {str(e)}")

    return str(check_comfyui_path(path))

# persistence.py

import json
from pathlib import Path
from filelock import FileLock, Timeout

import logging

logger = logging.getLogger(__name__)

# Default file paths for the environments database and its lock file.
DEFAULT_DB_FILE = "environments.json"
DEFAULT_LOCK_FILE = f"{DEFAULT_DB_FILE}.lock"


class PersistenceError(Exception):
    """Custom exception type for persistence-related errors."""

    pass


def load_environments(
    db_file: str = DEFAULT_DB_FILE, lock_file: str = DEFAULT_LOCK_FILE
) -> list:
    """
    Load environments from a JSON file with file locking.

    Args:
        db_file (str): Path to the JSON database file.
        lock_file (str): Path to the lock file.

    Returns:
        list: A list of environment dictionaries.

    Raises:
        PersistenceError: If the file lock cannot be acquired, if JSON decoding fails,
                        or if any other error occurs during loading.
    """
    environments = []
    lock = FileLock(lock_file, timeout=10)
    logger.info(f"Loading environments from {db_file}")
    try:
        with lock:
            if Path(db_file).exists():
                with open(db_file, "r") as f:
                    environments = json.load(f)
    except Timeout:
        logger.error("Could not acquire file lock for loading environments.")
        raise PersistenceError("Could not acquire file lock for loading environments.")
    except json.JSONDecodeError:
        logger.error("Error decoding JSON from environments file.")
        raise PersistenceError("Error decoding JSON from environments file.")
    except Exception as e:
        logger.error("An error occurred while loading environments: %s", e)
        raise PersistenceError(
            f"An error occurred while loading environments: {str(e)}"
        )

    return environments


def save_environments(
    environments: list,
    db_file: str = DEFAULT_DB_FILE,
    lock_file: str = DEFAULT_LOCK_FILE,
) -> None:
    """
    Save the list of environments to a JSON file with file locking.

    Args:
        environments (list): The list of environment dictionaries to save.
        db_file (str): Path to the JSON database file.
        lock_file (str): Path to the lock file.

    Raises:
        PersistenceError: If the file lock cannot be acquired or if any error occurs during saving.
    """
    lock = FileLock(lock_file, timeout=10)
    logger.info(f"Saving environments to {db_file}")
    try:
        with lock:
            with open(db_file, "w") as f:

                json.dump(environments, f, indent=4)
    except Timeout:
        logger.error("Could not acquire file lock for saving environments.")
        raise PersistenceError("Could not acquire file lock for saving environments.")
    except Exception as e:
        logger.error("An error occurred while saving environments: %s", e)
        raise PersistenceError(f"An error occurred while saving environments: {str(e)}")

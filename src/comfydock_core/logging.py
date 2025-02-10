import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """
    Creates and returns a logger with the specified name and consistent formatting.

    Args:
        name: The name of the logger (typically __name__ from the calling module)

    Returns:
        logging.Logger: Configured logger instance
    """
    logger = logging.getLogger(name)

    # Only add handlers if the logger doesn't have any
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Set default level to INFO
        logger.setLevel(logging.CRITICAL)

    return logger

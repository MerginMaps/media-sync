"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import logging
import pathlib
import sys
import typing

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# all media sync modules log to this logger, third party libraries keep their own loggers
logger = logging.getLogger("media-sync")


def setup_logger(
    log_path: typing.Optional[pathlib.Path] = None, log_verbosity: str = "INFO"
) -> logging.Logger:
    """Log messages to stdout and optionally also to a file with given verbosity, safe to call more than once"""
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    # handlers filter messages by their own level
    logger.setLevel(logging.DEBUG)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_path:
        file_handler = logging.FileHandler(log_path, mode="a")
        file_handler.setLevel(log_verbosity)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def handle_error_and_exit(error: typing.Union[str, Exception]) -> typing.NoReturn:
    logger.error(str(error))
    sys.exit(1)

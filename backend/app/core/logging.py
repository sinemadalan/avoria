import logging
from logging.handlers import RotatingFileHandler

from backend.app.core.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(settings: Settings) -> None:
    settings.log_directory.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level.upper())
    for existing_handler in root_logger.handlers[:]:
        root_logger.removeHandler(existing_handler)
        existing_handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        settings.log_directory / "avoria.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

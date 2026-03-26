from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


class Logger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        logger_name = f"ai-memory-hub:{self.log_dir}"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

            if os.environ.get("AI_MEMORY_LOG_TO_FILE") == "1":
                file_handler = RotatingFileHandler(
                    log_dir / "ai-memory.log",
                    maxBytes=10 * 1024 * 1024,
                    backupCount=5,
                )
                file_handler.setLevel(logging.INFO)
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)

    def info(self, message: str, **kwargs):
        self.logger.info(message, extra=kwargs if kwargs else {})

    def warning(self, message: str, **kwargs):
        self.logger.warning(message, extra=kwargs if kwargs else {})

    def error(self, message: str, **kwargs):
        self.logger.error(message, extra=kwargs if kwargs else {})

    def debug(self, message: str, **kwargs):
        self.logger.debug(message, extra=kwargs if kwargs else {})


_loggers: dict[str, Logger] = {}


def get_logger(log_dir: Path | None = None) -> Logger:
    key = str(log_dir.resolve()) if log_dir is not None else "__default__"
    if key not in _loggers:
        base_dir = log_dir if log_dir is not None else Path.cwd() / "logs"
        _loggers[key] = Logger(base_dir)
    return _loggers[key]

"""
Shared logger for the app. Import get_logger(__name__) anywhere you need
consistent, timestamped log output.
"""
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

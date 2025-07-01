import os

from loguru import logger


def is_root():
    if not hasattr(os, "getuid"):
        logger.warning("os.getuid() not available on your platform, assuming root")
        return True
    return getattr(os, "getuid")() == 0

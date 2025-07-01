from dataclasses import dataclass
import importlib
import pkgutil
import sys

from loguru import logger

import busio
import board
import digitalio
from adafruit_rgb_display.rgb import DisplaySPI


@dataclass
class DisplayConfig:
    display_type: str
    display_cs: str
    display_dc: str
    display_reset: str
    display_width: int
    display_height: int
    display_rotation: int
    display_baudrate: int
    display_x_offset: int
    display_y_offset: int


class Display:
    @staticmethod
    def create(cfg: DisplayConfig) -> DisplaySPI:
        spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        logger.debug("SPI initialized")
        # Import display library
        try:
            display_module = importlib.import_module(
                f"adafruit_rgb_display.{cfg.display_type.split('.')[0]}"
            )
        except ImportError as e:
            logger.error(
                f"Failed to import display module: {e}. Please ensure the adafruit_rgb_display library is installed."
            )
            logger.error(
                f"Possible modules: {(pkg.name for pkg in pkgutil.iter_modules(importlib.import_module('adafruit_rgb_display').__path__) if pkg.name != 'rgb')}"
            )
            sys.exit(1)
        if not hasattr(display_module, cfg.display_type.split(".")[1]):
            logger.error(
                f"Display type {cfg.display_type} not found in adafruit_rgb_display.{cfg.display_type.split('.')[0]}. Please check your configuration."
            )
            sys.exit(1)
        if not hasattr(board, f"{cfg.display_cs}"):
            logger.error(
                f"Display CS pin {cfg.display_cs} not found on in `CircuitPython:board`. Please check your configuration."
            )
            sys.exit(1)
        if not hasattr(board, f"{cfg.display_dc}"):
            logger.error(
                f"Display DC pin {cfg.display_dc} not found on in `CircuitPython:board`. Please check your configuration."
            )
            sys.exit(1)
        if not hasattr(board, f"{cfg.display_reset}"):
            logger.error(
                f"Display reset pin {cfg.display_reset} not found on in `CircuitPython:board`. Please check your configuration."
            )
            sys.exit(1)
        display: DisplaySPI = getattr(display_module, cfg.display_type.split(".")[1])(
            spi,
            cs=digitalio.DigitalInOut(getattr(board, f"{cfg.display_cs}")),
            dc=digitalio.DigitalInOut(getattr(board, f"{cfg.display_dc}")),
            rst=digitalio.DigitalInOut(getattr(board, f"{cfg.display_reset}")),
            width=cfg.display_width,
            height=cfg.display_height,
            rotation=cfg.display_rotation,
            baudrate=cfg.display_baudrate,
            x_offset=cfg.display_x_offset,
            y_offset=cfg.display_y_offset,
        )
        logger.debug("Display initialized")
        return display

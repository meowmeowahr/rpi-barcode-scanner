import importlib
import os
import pkgutil
import socket
import sys
import threading
import time
import json
from enum import Enum
import board
import digitalio
import busio
from adafruit_rgb_display.rgb import DisplaySPI
from PIL import Image, ImageDraw, ImageFont
from loguru import logger
from gpiozero import RotaryEncoder, Button, PWMOutputDevice
from gpiozero.pins.rpigpio import RPiGPIOFactory
from picamera2 import Picamera2
from pyzbar.pyzbar import decode
import neopixel
import elevate
import queue
import yaml
import argparse
from fasthid import Keyboard
from fasthid.hid.read import read_udc_gadget_suspended

from settings import FloatSetting, GroupSetting, IntSetting, StringOptionSetting
from vnc.vncserver import VNCClientThread, VNCConfig


# UI States
class UIState(Enum):
    IDLE = "IDLE"
    SCAN = "SCAN"
    TARGET_ADJUST_W = "TGT-H"
    TARGET_ADJUST_H = "TGT-W"
    SETTINGS = "SETTINGS"
    NULL = "NULL"


def is_root():
    return os.getuid() == 0


# GUI Class
class ScannerGui:
    def __init__(self):
        # Parse args
        parser = argparse.ArgumentParser(
            description="Raspberry Pi Optical Barcode Scanner with HID"
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose logging",
        )
        parser.add_argument(
            "--trace",
            action="store_true",
            help="Enable trace logging",
        )
        parser.add_argument(
            "--no-elevate",
            action="store_true",
            help="Do not attempt to elevate privileges",
        )
        parser.add_argument(
            "--config",
            type=str,
            default="config.yml",
            help="Path to the configuration file",
        )
        args = parser.parse_args()
        if args.trace:
            level = "TRACE"
        elif args.verbose:
            level = "DEBUG"
        else:
            level = "INFO"
        logger.remove()
        logger.add(
            sys.stderr,
            level=level,
        )

        logger.info("Initializing")

        if not is_root():
            if args.no_elevate:
                logger.error("Script must be run as root. Exiting.")
                sys.exit(1)
            logger.error("Script must be run as root. Attempting elevation.")
            elevate.elevate()

        # Load config
        with open(args.config, "r") as f:
            self.config = yaml.safe_load(f)

        self.device_config = self.config.get("device", {})
        self.hid_config = self.config.get("hid", {})
        self.gui_config = self.config.get("gui", {})

        self.led_config = self.device_config.get("led", {})
        self.buzzer_config = self.device_config.get("buzzer", {})
        self.display_config = self.device_config.get("display", {})
        self.encoder_config = self.device_config.get("encoder", {})
        self.trigger_config = self.device_config.get("trigger", {})
        self.camera_config = self.device_config.get("camera", {})
        self.vnc_config = self.device_config.get("vnc", {})

        self.led_pin = self.led_config.get("pin", 21)
        self.led_count = self.led_config.get("count", 16)

        self.buzzer_pin = self.buzzer_config.get("pin", 19)

        self.display_type = self.display_config.get("type", "st7789.ST7789")
        self.display_cs = self.display_config.get("cs", "CE0")
        self.display_dc = self.display_config.get("dc", "D25")
        self.display_reset = self.display_config.get("reset", "D24")
        self.display_width = self.display_config.get("width", 240)
        self.display_height = self.display_config.get("height", 240)
        self.display_rotation = self.display_config.get("rotation", 180)
        self.display_x_offset = self.display_config.get("x_offset", 0)
        self.display_y_offset = self.display_config.get("y_offset", 80)
        self.display_baudrate = self.display_config.get("baudrate", 60000000)

        self.encoder_pin_a = self.encoder_config.get("pin_a", 17)
        self.encoder_pin_b = self.encoder_config.get("pin_b", 18)
        self.encoder_button_config = self.encoder_config.get("button", {})
        self.encoder_button_pin = self.encoder_button_config.get("pin", 27)
        self.encoder_button_bounce_time = self.encoder_button_config.get(
            "bounce_time", 0.02
        )
        self.encoder_button_hold_time = self.encoder_button_config.get("hold_time", 0.5)
        self.encoder_button_pull_up = self.encoder_button_config.get("pull_up", True)

        self.trigger_button_pin = self.trigger_config.get("pin", 20)
        self.trigger_button_bounce_time = self.trigger_config.get("bounce_time", 0.02)
        self.trigger_button_pull_up = self.trigger_config.get("pull_up", True)

        self.camera_res = self.camera_config.get("resolution", (1920, 1080))

        self.toolbar_height = self.gui_config.get("toolbar_height", 30)

        self.toolbar_font_config = self.gui_config.get("toolbar_font", {})
        self.toolbar_font_size = self.toolbar_font_config.get("size", 10)
        self.toolbar_font_name = self.toolbar_font_config.get("name", "DejaVuSans.ttf")

        self.regular_font_config = self.gui_config.get("regular_font", {})
        self.regular_font_size = self.regular_font_config.get("size", 18)
        self.regular_font_name = self.regular_font_config.get("name", "DejaVuSans.ttf")

        self.vnc_enable = self.vnc_config.get("enable", True)
        self.vnc_port = self.vnc_config.get("port", 5900)
        self.vnc_bind = self.vnc_config.get("bind", "0.0.0.0")
        self.vnc_password = self.vnc_config.get("password", "")
        self.vnc_title = self.vnc_config.get("title", "Raspberry Pi Barcode Scanner")

        if self.vnc_enable and not self.vnc_password:
            logger.warning("VNC authentication is DISABLED")

        if "udc" not in self.hid_config:
            logger.warning(
                "No UDC configured in HID settings, using default '3f980000.usb', the default will not work on all systems, please check your UDC address in /sys/class/udc/"
            )
        self.hid_udc = self.hid_config.get("udc", "3f980000.usb")
        self.hid_path = self.hid_config.get("path", "/dev/hidg0")

        logger.debug("Loaded config")

        # Display setup
        spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        logger.debug("SPI initialized")
        # Import display library
        try:
            display_module = importlib.import_module(
                f"adafruit_rgb_display.{self.display_type.split('.')[0]}"
            )
        except ImportError as e:
            logger.error(
                f"Failed to import display module: {e}. Please ensure the adafruit_rgb_display library is installed."
            )
            logger.error(
                f"Possible modules: {(pkg.name for pkg in pkgutil.iter_modules(importlib.import_module('adafruit_rgb_display').__path__) if pkg.name != 'rgb')}"
            )
            sys.exit(1)
        if not hasattr(display_module, self.display_type.split(".")[1]):
            logger.error(
                f"Display type {self.display_type} not found in adafruit_rgb_display.{self.display_type.split('.')[0]}. Please check your configuration."
            )
            sys.exit(1)
        if not hasattr(board, f"{self.display_cs}"):
            logger.error(
                f"Display CS pin {self.display_cs} not found on in `CircuitPython:board`. Please check your configuration."
            )
            sys.exit(1)
        if not hasattr(board, f"{self.display_dc}"):
            logger.error(
                f"Display DC pin {self.display_dc} not found on in `CircuitPython:board`. Please check your configuration."
            )
            sys.exit(1)
        if not hasattr(board, f"{self.display_reset}"):
            logger.error(
                f"Display reset pin {self.display_reset} not found on in `CircuitPython:board`. Please check your configuration."
            )
            sys.exit(1)
        self.display: DisplaySPI = getattr(
            display_module, self.display_type.split(".")[1]
        )(
            spi,
            cs=digitalio.DigitalInOut(getattr(board, f"{self.display_cs}")),
            dc=digitalio.DigitalInOut(getattr(board, f"{self.display_dc}")),
            rst=digitalio.DigitalInOut(getattr(board, f"{self.display_reset}")),
            width=self.display_width,
            height=self.display_height,
            rotation=self.display_rotation,
            baudrate=self.display_baudrate,
            x_offset=self.display_x_offset,
            y_offset=self.display_y_offset,
        )
        logger.debug("Display initialized")

        # Encoder and button setup
        self.encoder = RotaryEncoder(self.encoder_pin_a, self.encoder_pin_b)
        self.encoder.when_rotated = self.on_encoder_turn
        self.encoder_button = Button(
            self.encoder_button_pin,
            pull_up=self.encoder_button_pull_up,
            bounce_time=self.encoder_button_bounce_time,
            hold_time=self.encoder_button_hold_time,
        )
        self.encoder_button.when_activated = self.on_button_press
        self.button_press_time = None

        self.trigger_button = Button(
            self.trigger_button_pin,
            pull_up=self.trigger_button_pull_up,
            bounce_time=self.trigger_button_bounce_time,
        )
        self.trigger_button.when_activated = self.on_trigger_press
        self.trigger_button.when_deactivated = self.on_trigger_release

        self.buzzer = PWMOutputDevice(
            self.buzzer_pin,
            pin_factory=RPiGPIOFactory(),
            frequency=440,
            active_high=True,
            initial_value=0,
        )

        logger.debug("Encoder and button initialized")

        if not hasattr(board, f"D{self.led_pin}"):
            logger.error(
                f"LED pin D{self.led_pin} not found on in `CircuitPython:board`. Please check your configuration."
            )
            sys.exit(1)
        self.led = neopixel.NeoPixel(
            getattr(board, f"D{self.led_pin}"),
            self.led_count,
            brightness=0.2,
            auto_write=False,
        )
        self.led_rgb = [0, 0, 0]
        logger.debug("Led initialized")

        self.picam2 = Picamera2()
        config = self.picam2.create_preview_configuration(
            main={"size": self.camera_res, "format": "BGR888"}
        )
        self.picam2.configure(config)
        self.picam2.start()
        logger.debug("Camera initialized")

        # Image buffer and lock
        self.viewfinder = Image.new("RGB", (self.display.width, self.display.height))
        self.image_lock = threading.Lock()
        self.state = UIState.IDLE

        # Init target, will be replaced when settings are loaded
        self.target_width = 100
        self.target_height = 50

        # Settings index for settings menu
        self.settings_stack = []  # Navigation stack
        self.settings_index = 0  # Current cursor
        self.active_setting = None  # Currently selected for editing

        self.barcodes = []

        # HID setup
        self.udc_connected = False

        # Settings setup
        self.hid_thread: threading.Thread | None = None
        self.settings_lock = threading.Lock()
        self.settings = [
            StringOptionSetting(
                id="connection",
                name="Connection",
                options=["USB", "NONE"],
                default_value="USB",
                value="USB",
                apply_callback=self.apply_connection,
            ),
            FloatSetting(
                id="brightness",
                name="Brightness",
                min_value=-1.0,
                max_value=1.0,
                default_value=0.0,
                value=0.0,
                apply_callback=self.apply_brightness,
            ),
            FloatSetting(
                id="contrast",
                name="Contrast",
                min_value=0.0,
                max_value=2.0,
                default_value=1.0,
                value=1.0,
                apply_callback=self.apply_contrast,
            ),
            FloatSetting(
                id="exposure",
                name="Exposure",
                min_value=-8.0,
                max_value=8.0,
                default_value=0.0,
                value=0.0,
                apply_callback=self.apply_exposure,
            ),
            FloatSetting(
                id="gain",
                name="Gain",
                min_value=0.0,
                max_value=16.0,
                default_value=1.0,
                value=1.0,
                apply_callback=lambda v: self.picam2.set_controls({"AnalogueGain": v}),
                precision=2,
                step=0.1,
            ),
            IntSetting(
                id="ae",
                name="AEC/AGC",
                min_value=0,
                max_value=1,
                default_value=1,
                value=1,
                apply_callback=lambda v: self.picam2.set_controls(
                    {"AeEnable": bool(v)}
                ),
                step=1,
            ),
            FloatSetting(
                id="sharpness",
                name="Sharpness",
                min_value=0.0,
                max_value=16.0,
                default_value=0.0,
                value=0.0,
                apply_callback=self.apply_sharpness,
            ),
            GroupSetting(
                id="led",
                name="LED Control",
                apply_callback=lambda: None,
                default_value=None,
                value=None,
                children=[
                    FloatSetting(
                        id="led",
                        name="LED Bright",
                        min_value=0.0,
                        max_value=1.0,
                        default_value=0.2,
                        value=0.2,
                        apply_callback=self.apply_led_bright,
                        precision=2,
                        step=0.05,
                    ),
                    IntSetting(
                        id="led_red",
                        name="LED Red",
                        min_value=0,
                        max_value=255,
                        default_value=255,
                        value=255,
                        apply_callback=self.apply_led(0),
                        step=5,
                    ),
                    IntSetting(
                        id="led_green",
                        name="LED Green",
                        min_value=0,
                        max_value=255,
                        default_value=255,
                        value=255,
                        apply_callback=self.apply_led(1),
                        step=5,
                    ),
                    IntSetting(
                        id="led_blue",
                        name="LED Blue",
                        min_value=0,
                        max_value=255,
                        default_value=255,
                        value=255,
                        apply_callback=self.apply_led(2),
                        step=5,
                    ),
                ],
            ),
            IntSetting(
                id="tgt_width",
                name="Target Width",
                min_value=self.display.width // 6,
                max_value=int(self.display.width // 1.2),
                default_value=self.display.width // 2,
                value=self.display.width // 2,
                apply_callback=self.apply_target_width,
                step=1,
            ),
            IntSetting(
                id="tgt_height",
                name="Target Height",
                min_value=self.display.height // 6,
                max_value=int(self.display.height // 1.2),
                default_value=self.display.height // 3,
                value=self.display.width // 3,
                apply_callback=self.apply_target_height,
                step=1,
            ),
        ]
        self.load_settings()
        for setting in self.settings:
            setting.apply()
        logger.debug("Settings initialized")

        # Font for settings
        self.font = ImageFont.truetype(self.regular_font_name, self.regular_font_size)
        self.tb_font = ImageFont.truetype(
            self.toolbar_font_name, self.toolbar_font_size
        )
        logger.debug("Font loaded")

        tones = [523, 659, 784, 1047]
        for freq in tones:
            self.buzzer.frequency = freq
            self.buzzer.value = 0.5
            time.sleep(0.3)
        self.buzzer.value = 0

        # Start threads
        self.running = True

        self.barcode_queue = queue.Queue()
        self.hid_thread = threading.Thread(
            target=self.hid_sender_thread, args=(self.barcode_queue,)
        )
        self.hid_thread.start()
        self.hid_conn_check_thread = threading.Thread(
            target=self.hid_connection_check, args=(self.hid_udc,)
        )
        self.hid_conn_check_thread.start()

        # before display thread
        if self.vnc_enable:
            self.vnc_image = Image.new("RGB", (self.display.width, self.display.height))
            self.vnc_thread = threading.Thread(target=self.vnc_server_thread)
            self.vnc_thread.start()

        self.image_thread = threading.Thread(target=self.image_update_thread)
        self.display_thread = threading.Thread(target=self.display_update_thread)
        self.image_thread.start()
        self.display_thread.start()

        logger.info("Threads started")

    @property
    def visible_settings(self):
        if not self.settings_stack:
            return self.settings
        return [self.make_exit_setting()] + self.settings_stack[-1].children

    def make_exit_setting(self):
        return StringOptionSetting(
            id="exit",
            name="Exit",
            default_value="",
            value="",
            apply_callback=lambda v: None,
            options=[""],
        )

    def tone(self, notes: list[tuple[int, float]]):
        def target():
            """Play a tone for a given duration."""
            for note, duration in notes:
                self.buzzer.frequency = note
                self.buzzer.value = 0.5
                time.sleep(duration)
            self.buzzer.value = 0

        threading.Thread(target=target, daemon=True, name="TonePlayer").start()

    def apply_connection(self, value: str):
        logger.info(f"Connection set to {value}")
        if value == "USB" and self.hid_thread and not self.hid_thread.is_alive():
            self.hid_thread.start()
            logger.debug("Restarted HID sender process")

    def apply_target_width(self, value: int):
        """Example callback for target width."""
        logger.info(f"Set target width to {value}")
        self.target_width = value

    def apply_target_height(self, value: int):
        """Example callback for target height."""
        logger.info(f"Set target height to {value}")
        self.target_height = value

    def apply_brightness(self, value):
        """Example callback for brightness."""
        logger.info(f"Set brightness to {value}")
        self.picam2.set_controls({"Brightness": value})

    def apply_contrast(self, value):
        """Example callback for contrast."""
        logger.info(f"Set contrast to {value}")
        self.picam2.set_controls({"Contrast": value})

    def apply_exposure(self, value: float):
        """Example callback for exposure."""
        logger.info(f"Set exposure to {value}")
        # Convert to Picamera2 control value
        self.picam2.set_controls({"ExposureValue": value})

    def apply_sharpness(self, value: float):
        """Set sharpness."""
        logger.info(f"Set sharpness to {value}")
        self.picam2.set_controls({"Sharpness": value})

    def apply_led_bright(self, value: float):
        logger.info(f"Set led to {value}")
        self.led.brightness = value
        self.led.show()

    def apply_led(self, index: int):
        def updater(value: int):
            logger.info(f"Set LED channel {index} to {value}")
            self.led_rgb[index] = value
            self.led.fill(self.led_rgb)
            self.led.write()

        return updater

    def flatten_settings(self, settings):
        flat = []
        for s in settings:
            if isinstance(s, GroupSetting):
                flat.extend(self.flatten_settings(s.children))
            else:
                flat.append(s)
        return flat

    def load_settings(self):
        """Load settings from JSON file."""
        try:
            with open("settings.json", "r") as f:
                data = json.load(f)

            all_settings = self.flatten_settings(self.settings)
            for saved in data:
                for setting in all_settings:
                    if saved["id"] == setting.id:
                        setting.value = saved["value"]
                        logger.debug(f"Loaded setting {setting.id}: {setting.value}")
                        break
        except FileNotFoundError:
            logger.debug("No settings file found, using defaults")
        except Exception as e:
            logger.error(f"Error loading settings: {e}")

    def save_settings(self):
        """Save settings to JSON file."""
        try:
            with open("settings.json", "w") as f:
                json.dump(
                    [s.to_dict() for s in self.flatten_settings(self.settings)],
                    f,
                    indent=2,
                )
            logger.debug("Settings saved")
        except Exception as e:
            logger.exception(e)

    def on_encoder_turn(self, enc: RotaryEncoder):
        """Handle encoder rotation."""
        delta = 1 if enc.value > 0 else -1
        logger.debug(f"Encoder turned: delta={delta}, raw={enc.value}")
        enc.value = 0
        with self.image_lock:
            if self.state == UIState.TARGET_ADJUST_W:
                width = max(10, min(200, self.target_width + delta * 5))
                for setting in self.settings:
                    if setting.id == "tgt_width":
                        setting.value = width
                        setting.apply()
                        self.target_width = width
                        self.save_settings()
                        break
                logger.debug(f"Target width adjusted to {self.target_width}")
            elif self.state == UIState.TARGET_ADJUST_H:
                height = max(10, min(200, self.target_height + delta * 5))
                for setting in self.settings:
                    if setting.id == "tgt_height":
                        setting.value = height
                        setting.apply()
                        self.target_height = height
                        self.save_settings()
                        break
                logger.debug(f"Target height adjusted to {self.target_height}")
            elif self.state == UIState.SETTINGS:
                visible = self.visible_settings

                if self.active_setting is None:
                    # Not editing – scroll cursor
                    self.settings_index = max(
                        0, min(len(visible) - 1, self.settings_index + delta)
                    )
                else:
                    # Editing active setting – change value
                    s = self.active_setting
                    if isinstance(s, FloatSetting):
                        s.value = max(
                            s.min_value,
                            min(
                                s.max_value,
                                round(s.value + delta * s.step, s.precision),
                            ),
                        )
                    elif isinstance(s, IntSetting):
                        s.value = max(
                            s.min_value, min(s.max_value, s.value + delta * s.step)
                        )
                    elif isinstance(s, StringOptionSetting):
                        idx = s.options.index(s.value)
                        idx = (idx + delta) % len(s.options)
                        s.value = s.options[idx]
                    s.apply()
                    self.save_settings()

    def on_button_press(self):
        """Handle button press (start timing for short/long detection)."""
        self.button_press_time = time.time()
        logger.debug("Button pressed")

    def handle_button(self):
        """Handle encoder button short/long press."""
        if self.button_press_time:
            now = time.time()
            short_press = (now - self.button_press_time) < self.encoder_button.hold_time
            long_press = (
                now - self.button_press_time
            ) > self.encoder_button.hold_time and self.encoder_button.is_held

            if short_press and not self.encoder_button.is_active:
                # Short press
                if self.state == UIState.SETTINGS:
                    visible = self.visible_settings
                    selected = visible[self.settings_index]

                    if self.active_setting:
                        # Deselect setting
                        self.active_setting = None
                    elif selected.id == "exit":
                        if self.settings_stack:
                            self.settings_stack.pop()
                            self.settings_index = 0
                        else:
                            self.state = UIState.IDLE
                            self.settings_stack.clear()
                            self.settings_index = 0
                    elif isinstance(selected, GroupSetting):
                        self.settings_stack.append(selected)
                        self.settings_index = 0
                    else:
                        self.active_setting = selected
                elif self.state == UIState.IDLE:
                    self.state = UIState.TARGET_ADJUST_W
                elif self.state == UIState.TARGET_ADJUST_W:
                    self.state = UIState.TARGET_ADJUST_H
                elif self.state == UIState.TARGET_ADJUST_H:
                    self.state = UIState.IDLE

                logger.info(f"State changed to {self.state}")
                self.button_press_time = None

            elif long_press:
                logger.debug("Long press detected")
                with self.image_lock:
                    if self.state == UIState.IDLE:
                        self.state = UIState.SETTINGS
                        self.settings_stack.clear()
                        self.settings_index = 0
                        self.active_setting = None
                    elif self.state == UIState.SETTINGS:
                        if self.settings_stack:
                            self.settings_stack.pop()  # Exit submenu
                            self.settings_index = 0
                            self.active_setting = None
                        else:
                            self.state = UIState.IDLE
                            self.active_setting = None

                    logger.info(f"State changed to {self.state}")
                self.button_press_time = None

    def on_trigger_press(self):
        logger.debug("Trigger pressed")
        if self.state == UIState.IDLE:
            self.state = UIState.SCAN
        elif self.state == UIState.SETTINGS:
            self.state = UIState.IDLE

    def on_trigger_release(self):
        logger.debug("Trigger released")
        if self.state == UIState.SCAN:
            self.state = UIState.IDLE

    def hid_sender_thread(self, in_queue: queue.Queue):
        kb = Keyboard(self.hid_path)
        while self.running:
            try:
                code = in_queue.get(timeout=0.1)
                connection = next(
                    (s for s in self.settings if s.id == "connection"), None
                )
                if connection and connection.value == "USB":
                    logger.debug(f"Sending barcode over HID: {code}")
                    kb.type(code, 0)
            except queue.Empty:
                continue

    def hid_connection_check(self, udc_path: str):
        while self.running:
            self.udc_connected = not read_udc_gadget_suspended(udc_path)
            time.sleep(0.5)

    def vnc_server_thread(self):
        vnc_config = VNCConfig(
            vnc_password=self.vnc_password,
            win_title=self.vnc_title,
        )

        sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sockServer.bind((self.vnc_bind, self.vnc_port))

        logger.debug(
            "VNC server started"
        )
        while True:
            sockServer.listen(4)
            (conn, (ip, port)) = sockServer.accept()

            def image_source():
                return self.vnc_image

            newthread = VNCClientThread(
                sock=conn,
                image_source=image_source,
                ip=ip,
                port=port,
                vnc_config=vnc_config,
            )
            newthread.daemon = True
            newthread.start()

    def image_update_thread(self):
        """Simulate image updates with random colors."""
        while self.running:
            pixels = self.picam2.capture_array()
            image = Image.fromarray(pixels)

            # Resize camera image to fit the bottom of the display, leaving 10px at the top for the toolbar
            vf_height = self.display.height - self.toolbar_height
            vf_image = image.resize(
                (self.display.width, vf_height), Image.Resampling.NEAREST
            )
            # Create a new blank image for the full display
            full_img = Image.new("RGB", (self.display.width, self.display.height))
            # Paste the viewfinder at the bottom (y=toolbar height)
            full_img.paste(vf_image, (0, self.toolbar_height))
            self.viewfinder = full_img

            if self.state == UIState.IDLE:
                self.barcodes = []
            elif self.state == UIState.SCAN:
                img_w, img_h = image.size
                disp_w = self.display.width
                vf_height = self.display.height - self.toolbar_height  # 210
                scale_x = img_w / disp_w
                scale_y = img_h / vf_height

                y0_disp_vf = (vf_height - self.target_height) // 2
                y0_disp = self.toolbar_height + y0_disp_vf
                x0_disp = (disp_w - self.target_width) // 2
                x1_disp = x0_disp + self.target_width

                x0 = int(x0_disp * scale_x)
                y0 = int(y0_disp_vf * scale_y)  # relative to vf
                x1 = int(x1_disp * scale_x)
                y1 = int((y0_disp_vf + self.target_height) * scale_y)

                # Clamp to image bounds
                x0 = max(0, min(img_w, x0))
                y0 = max(0, min(img_h, y0))
                x1 = max(0, min(img_w, x1))
                y1 = max(0, min(img_h, y1))

                scan_crop = image.crop((x0, y0, x1, y1))
                self.barcodes = decode(scan_crop)

                if self.barcodes:
                    self.state = UIState.IDLE
                    self.tone(
                        [(4000, 0.1), (3000, 0.1)]
                    )  # Play a tone for successful scan
                    logger.info(f"Found {len(self.barcodes)} barcodes")

                # determine which barcode is closest to the center of the target rectangle
                if self.barcodes:
                    target_center_disp = (
                        x0_disp + self.target_width // 2,
                        y0_disp + self.target_height // 2,
                    )

                    def rect_center(rect):
                        return (
                            rect.left + rect.width // 2,
                            rect.top + rect.height // 2,
                        )

                    closest_barcode = min(
                        self.barcodes,
                        key=lambda b: (rect_center(b.rect)[0] - target_center_disp[0])
                        ** 2
                        + (rect_center(b.rect)[1] - target_center_disp[1]) ** 2,
                    )
                    logger.info(
                        f"Closest barcode: {closest_barcode.data.decode('utf-8')} at {closest_barcode.rect}"
                    )
                    barcode_str = closest_barcode.data.decode("utf-8")
                    self.barcode_queue.put(barcode_str)

                # Draw barcode bounds on the viewfinder image
                for barcode in self.barcodes:
                    bx, by, bw, bh = barcode.rect
                    crop_w, crop_h = scan_crop.size

                    # Reuse same disp_x0 and y0_disp as above
                    scale_x_disp = self.target_width / crop_w
                    scale_y_disp = self.target_height / crop_h

                    disp_bx0 = int(x0_disp + bx * scale_x_disp)
                    disp_by0 = int(y0_disp + by * scale_y_disp)
                    disp_bx1 = int(disp_bx0 + bw * scale_x_disp)
                    disp_by1 = int(disp_by0 + bh * scale_y_disp)

                    draw = ImageDraw.Draw(self.viewfinder)
                    draw.rectangle(
                        [disp_bx0, disp_by0, disp_bx1, disp_by1], fill="lime", width=3
                    )

    def get_visible_menu_items(self, menu_list, current_index, visible_count=2):
        """
        Get up to visible_count menu items centered around current_index,
        without wrapping around.

        Returns:
            list of (index, item): Actual indices with corresponding menu items.
        """
        if not menu_list:
            return []

        total = len(menu_list)
        visible_count = min(visible_count, total)

        # Clamp current index to valid range
        current_index = max(0, min(current_index, total - 1))

        # Calculate bounds for visible items
        half = visible_count // 2
        start = current_index - half
        end = start + visible_count

        # Clamp window to list bounds
        if start < 0:
            start = 0
            end = visible_count
        if end > total:
            end = total
            start = total - visible_count

        return [(i, menu_list[i]) for i in range(start, end)]

    def display_update_thread(self):
        """Update display at 25 FPS."""
        target_fps = 15
        frame_time = 1.0 / target_fps
        while self.running:
            start_time = time.time()
            with self.image_lock:
                # Create a copy of the image
                img = self.viewfinder.copy()
                draw = ImageDraw.Draw(img)

                # draw toolbar
                draw.rectangle(
                    (0, 0, self.display.width, self.toolbar_height), fill="black"
                )
                # Center toolbar text vertically
                state_text = f"State: {self.state.value}"
                text_bbox = self.tb_font.getbbox(state_text)
                text_height = text_bbox[3] - text_bbox[1]
                y = (self.toolbar_height - text_height) // 2
                draw.text((10, y), state_text, font=self.tb_font, fill="white")

                # draw right-aligned UDC text
                connection = next(
                    (s for s in self.settings if s.id == "connection"), None
                )
                if connection and connection.value == "USB":
                    conn_text = f"Conn: {'OK' if self.udc_connected else 'NO'}"
                    text_bbox = self.tb_font.getbbox(conn_text)
                    draw.text(
                        (
                            self.display.width - text_bbox[2] - 10,
                            (self.toolbar_height - text_bbox[3] + text_bbox[1]) // 2,
                        ),
                        conn_text,
                        font=self.tb_font,
                        fill="green" if self.udc_connected else "red",
                    )

                if self.state in [
                    UIState.IDLE,
                    UIState.SCAN,
                    UIState.TARGET_ADJUST_W,
                    UIState.TARGET_ADJUST_H,
                ]:
                    # Draw centered target rectangle
                    x0 = (self.display.width - self.target_width) // 2
                    y0 = (
                        self.toolbar_height
                        + (
                            (self.display.height - self.toolbar_height)
                            - self.target_height
                        )
                        // 2
                    )
                    x1 = x0 + self.target_width
                    y1 = y0 + self.target_height

                    match self.state:
                        case UIState.IDLE:
                            color = "red"
                        case UIState.SCAN:
                            color = "blue"
                        case _:
                            color = "yellow"
                    draw.rectangle((x0, y0, x1, y1), outline=color, width=3)
                    # draw crosshair
                    draw.line(
                        (
                            x0 + self.target_width // 2,
                            y0,
                            x0 + self.target_width // 2,
                            y1,
                        ),
                        fill=color,
                        width=1,
                    )
                    draw.line(
                        (
                            x0,
                            y0 + self.target_height // 2,
                            x1,
                            y0 + self.target_height // 2,
                        ),
                        fill=color,
                        width=1,
                    )
                elif self.state == UIState.SETTINGS:
                    # Draw settings menu in bottom 120px
                    overlay = Image.new("RGBA", self.viewfinder.size, (0, 0, 0, 0))
                    draw_overlay = ImageDraw.Draw(overlay)

                    draw_overlay.rectangle(
                        (
                            0,
                            self.display.height // 1.4,
                            self.display.width,
                            self.display.height,
                        ),
                        fill=(0, 0, 0, 200),
                    )
                    with self.settings_lock:
                        visible_count = 2
                        visible_settings = self.get_visible_menu_items(
                            self.visible_settings, self.settings_index, visible_count=2
                        )

                        # Draw setting text
                        for draw_index, (i, setting) in enumerate(visible_settings):
                            color = "white"
                            if i == self.settings_index:
                                if setting == self.active_setting:
                                    color = "cyan"
                                else:
                                    color = "yellow"

                            if setting.id == "exit":
                                state_text = "[Exit]"
                            elif isinstance(setting, GroupSetting):
                                state_text = f"[{setting.name}]"
                            elif isinstance(setting, FloatSetting):
                                state_text = f"{setting.name}: {round(setting.value, setting.precision)}{setting.suffix}"
                            elif isinstance(setting, IntSetting):
                                state_text = (
                                    f"{setting.name}: {setting.value}{setting.suffix}"
                                )
                            elif isinstance(setting, StringOptionSetting):
                                state_text = f"{setting.name}: {setting.value}"
                            else:
                                logger.error(f"Unrecognized setting, {setting}")
                                state_text = "ERROR"
                            draw_overlay.text(
                                (10, self.display.height // 1.4 + 10 + draw_index * 30),
                                state_text,
                                font=self.font,
                                fill=color,
                            )

                        # Draw scroll indicator
                        total = len(self.visible_settings)
                        if total > visible_count:
                            scrollbar_top = int(self.display.height // 1.4) + 5
                            scrollbar_bottom = self.display.height - 5
                            scrollbar_height = scrollbar_bottom - scrollbar_top

                            track_height = scrollbar_height
                            thumb_height = max(
                                10, int(track_height * (visible_count / total))
                            )

                            # Position of thumb (top-aligned proportional to index)
                            max_scroll = total - visible_count
                            scroll_pos = min(
                                max(self.settings_index - (visible_count // 2), 0),
                                max_scroll,
                            )
                            scroll_ratio = (
                                scroll_pos / max_scroll if max_scroll > 0 else 0
                            )
                            thumb_top = scrollbar_top + int(
                                (track_height - thumb_height) * scroll_ratio
                            )
                            thumb_bottom = thumb_top + thumb_height

                            # Draw the thumb on the far right
                            draw_overlay.rectangle(
                                (230, thumb_top, 235, thumb_bottom), fill="gray"
                            )

                        img = Image.alpha_composite(
                            img.convert("RGBA"), overlay
                        ).convert("RGB")

            # Update display
            if self.vnc_enable:
                self.vnc_image = img.copy()
            self.display.image(img)
            # logger.debug("Display updated")

            # Maintain FPS
            elapsed = time.time() - start_time
            sleep_time = max(0, frame_time - elapsed)
            time.sleep(sleep_time)
            logger.trace(f"FPS: {1.0 / (time.time() - start_time):.2f}")

    def run(self):
        """Main loop for button handling."""
        try:
            while self.running:
                self.handle_button()
                time.sleep(0.01)
        except KeyboardInterrupt:
            self.state = UIState.NULL
            logger.info("Shutting down")
            self.running = False
            self.image_thread.join()
            self.display_thread.join()
            logger.info("Threads stopped")

            red_image = Image.new(
                "RGB", (self.display.width, self.display.height), "red"
            )
            draw = ImageDraw.Draw(red_image)
            draw.line(
                (0, 0, self.display.width, self.display.height), fill="white", width=10
            )
            draw.line(
                (self.display.width, 0, 0, self.display.height), fill="white", width=10
            )
            self.display.image(red_image)
            self.led.fill((0, 0, 0))
            self.led.show()


if __name__ == "__main__":
    gui = ScannerGui()
    gui.run()

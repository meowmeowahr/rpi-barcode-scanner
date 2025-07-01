import argparse
import subprocess
import sys
import threading
import time
import json

from loguru import logger
import elevate
import yaml

from PIL import Image, ImageDraw

import board
import neopixel
from gpiozero import RotaryEncoder, Button
from picamera2 import Picamera2
from pyzbar.pyzbar import decode

from hid import HIDInterface
from vnc.vncserver import VNCConfig

from state import UIState
from settings import (
    ButtonMenuSetting,
    FloatSetting,
    GroupSetting,
    IntSetting,
    StringOptionSetting,
)
from tone import TonePlayer
from display import Display, DisplayConfig
from ui import UserInterface, FontConfig, DisplayInfo, UiParams, ConnectionData
from ui_interface import UserInterfaceInputController
from remoteview import vnc_server_thread
from util import is_root


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
        self.visible_settings = self.gui_config.get("menu_items", 3)

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
        elif self.vnc_enable:
            logger.warning(
                "VNC connection in unencrypted. Do not use VNC in production."
            )

        if "udc" not in self.hid_config:
            logger.warning(
                "No UDC configured in HID settings, using default '3f980000.usb', the default will not work on all systems, please check your UDC address in /sys/class/udc/"
            )
        self.hid_udc = self.hid_config.get("udc", "3f980000.usb")
        self.hid_path = self.hid_config.get("path", "/dev/hidg0")

        logger.debug("Loaded config")

        # Display setup
        self.display = Display.create(
            DisplayConfig(
                display_type=self.display_config.get("type", "st7789.ST7789"),
                display_cs=self.display_config.get("cs", "CE0"),
                display_dc=self.display_config.get("dc", "D25"),
                display_reset=self.display_config.get("reset", "D24"),
                display_width=self.display_config.get("width", 240),
                display_height=self.display_config.get("height", 240),
                display_rotation=self.display_config.get("rotation", 180),
                display_baudrate=self.display_config.get("baudrate", 60000000),
                display_x_offset=self.display_config.get("x_offset", 0),
                display_y_offset=self.display_config.get("y_offset", 80),
            )
        )

        # Encoder and button setup
        self.encoder = RotaryEncoder(self.encoder_pin_a, self.encoder_pin_b)
        self.encoder_button = Button(
            self.encoder_button_pin,
            pull_up=self.encoder_button_pull_up,
            bounce_time=self.encoder_button_bounce_time,
            hold_time=self.encoder_button_hold_time,
        )

        self.trigger_button = Button(
            self.trigger_button_pin,
            pull_up=self.trigger_button_pull_up,
            bounce_time=self.trigger_button_bounce_time,
        )

        self.buzzer = TonePlayer(self.buzzer_pin)

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
        self.state = UIState.IDLE

        # Init target, will be replaced when settings are loaded
        self.target_width = 100
        self.target_height = 50

        self.barcodes = []

        # HID setup
        def check_connection():
            option = next((s for s in self.settings if s.id == "connection"))
            return option and option.value == "USB"
        self.hid = HIDInterface(self.hid_udc, self.hid_path, lambda: check_connection)

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
            GroupSetting(
                id="camera",
                name="Camera Settings",
                apply_callback=lambda: None,
                default_value=None,
                value=None,
                children=[
                    FloatSetting(
                        id="brightness",
                        name="Brightness",
                        min_value=-1.0,
                        max_value=1.0,
                        default_value=0.0,
                        value=0.0,
                        apply_callback=lambda v: self.picam2.set_controls(
                            {"Brightness": v}
                        ),
                    ),
                    FloatSetting(
                        id="contrast",
                        name="Contrast",
                        min_value=0.0,
                        max_value=2.0,
                        default_value=1.0,
                        value=1.0,
                        apply_callback=lambda v: self.picam2.set_controls(
                            {"Contrast": v}
                        ),
                    ),
                    FloatSetting(
                        id="exposure",
                        name="Exposure",
                        min_value=-8.0,
                        max_value=8.0,
                        default_value=0.0,
                        value=0.0,
                        apply_callback=lambda v: self.picam2.set_controls(
                            {"ExposureValue": v}
                        ),
                    ),
                    FloatSetting(
                        id="gain",
                        name="Gain",
                        min_value=0.0,
                        max_value=16.0,
                        default_value=1.0,
                        value=1.0,
                        apply_callback=lambda v: self.picam2.set_controls(
                            {"AnalogueGain": v}
                        ),
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
                        apply_callback=lambda v: self.picam2.set_controls(
                            {"Sharpness": v}
                        ),
                    ),
                    FloatSetting(
                        id="saturation",
                        name="Saturation",
                        min_value=0.0,
                        max_value=16.0,
                        default_value=0.0,
                        value=0.0,
                        apply_callback=lambda v: self.picam2.set_controls(
                            {"Saturation": v}
                        ),
                    ),
                ],
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
            GroupSetting(
                id="target",
                name="Target Settings",
                value=None,
                default_value=None,
                apply_callback=lambda: None,
                children=[
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
                ],
            ),
            GroupSetting(
                id="hid",
                name="HID Settings",
                default_value=None,
                value=None,
                apply_callback=lambda: None,
                children=[
                    FloatSetting(
                        id="hid_delay",
                        name="Key Delay",
                        default_value=0.0,
                        value=0.0,
                        min_value=0.0,
                        max_value=1.0,
                        precision=2,
                        step=0.01,
                        suffix="s",
                        apply_callback=self.hid.apply_delay,
                    )
                ]
            ),
            ButtonMenuSetting(
                id="shutdown",
                name="Shutdown",
                apply_callback=self.shutdown,
                default_value=None,
                value=None,
            ),
        ]
        self.load_settings()
        for setting in self.settings:
            if not isinstance(setting, ButtonMenuSetting):
                setting.apply()
        logger.debug("Settings initialized")

        # Create UI
        self.ui = UserInterface(
            self,
            FontConfig(
                toolbar_font_name=self.toolbar_font_name,
                toolbar_font_size=self.toolbar_font_size,
                regular_font_name=self.regular_font_name,
                regular_font_size=self.regular_font_size,
            ),
            DisplayInfo(width=self.display.width, height=self.display.height),
        )
        self.ui_controller = UserInterfaceInputController(
            self.ui,
            self,
            self.settings,
            self.encoder,
            self.encoder_button,
            self.trigger_button,
        )
        logger.debug("UI loaded")

        tones = [523, 659, 784, 1047]
        for freq in tones:
            self.buzzer.tone(freq, 0.3)

        # Start threads
        self.running = True
        self.shutdown_flag = False

        # before display thread
        if self.vnc_enable:
            self.vnc_image = Image.new("RGB", (self.display.width, self.display.height))
            self.vnc_thread = threading.Thread(
                target=vnc_server_thread,
                args=(
                    VNCConfig(self.vnc_password, self.vnc_title),
                    self.vnc_bind,
                    self.vnc_port,
                    lambda: self.vnc_image,
                ),
                daemon=True,
            )
            self.vnc_thread.start()

        self.image_thread = threading.Thread(target=self.image_update_thread)
        self.display_thread = threading.Thread(target=self.display_update_thread)
        self.image_thread.start()
        self.display_thread.start()

        logger.info("Threads started")

    def send_barcode(self, barcode: str):
        option = next((s for s in self.settings if s.id == "connection"))
        if not option:
            return
        if option.value == "USB":
            self.hid.send(barcode)

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

    def image_update_thread(self):
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
                    self.buzzer.tones([(3000, 0.1), (4000, 0.1)])
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
                    self.send_barcode(barcode_str)

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

    def display_update_thread(self):
        """Update display at 25 FPS."""
        target_fps = 15
        frame_time = 1.0 / target_fps
        while self.running:
            start_time = time.time()
            with self.ui.image_lock:
                img = self.ui.draw(
                    self.viewfinder,
                    self.settings,
                    ConnectionData(udc_connected=self.hid.udc_connected),
                    UiParams(
                        toolbar_height=self.toolbar_height,
                        target_width=self.target_width,
                        target_height=self.target_height,
                        visible_settings=self.visible_settings,
                    ),
                    self.state,
                    self.settings_lock,
                )
            # Update display
            if self.vnc_enable:
                self.vnc_image = img.copy()
            self.display.image(img)

            # Maintain FPS
            elapsed = time.time() - start_time
            sleep_time = max(0, frame_time - elapsed)
            time.sleep(sleep_time)
            logger.trace(f"FPS: {1.0 / (time.time() - start_time):.2f}")

    def shutdown(self):
        self.shutdown_flag = True

    def run(self) -> int:
        """Main loop for button handling."""
        try:
            while self.running:
                self.ui_controller.handle_button()
                time.sleep(0.01)
                if self.shutdown_flag:
                    return 2
        except KeyboardInterrupt:
            self.state = UIState.NULL
            logger.info("Shutting down")
            self.running = False
            self.image_thread.join()
            self.display_thread.join()
            logger.info("Threads stopped")
            return 1
        finally:
            return 1


if __name__ == "__main__":
    gui = ScannerGui()
    ret = gui.run()
    if ret == 1:
        red_image = Image.new("RGB", (gui.display.width, gui.display.height), "red")
        draw = ImageDraw.Draw(red_image)
        draw.line((0, 0, gui.display.width, gui.display.height), fill="white", width=10)
        draw.line((gui.display.width, 0, 0, gui.display.height), fill="white", width=10)
        gui.display.image(red_image)
        gui.led.fill((0, 0, 0))
        gui.led.show()
    if ret == 2:
        red_image = Image.new("RGB", (gui.display.width, gui.display.height), "blue")
        draw = ImageDraw.Draw(red_image)
        draw.line((0, 0, gui.display.width, gui.display.height), fill="white", width=10)
        draw.line((gui.display.width, 0, 0, gui.display.height), fill="white", width=10)
        gui.display.image(red_image)
        gui.led.fill((0, 0, 0))
        gui.led.show()
        time.sleep(0.5)
        subprocess.run(["systemctl", "poweroff"])

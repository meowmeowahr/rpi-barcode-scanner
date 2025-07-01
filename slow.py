import time
import board
import digitalio
import json
from dataclasses import dataclass
from typing import Callable, Tuple
from pathlib import Path
from picamera2 import Picamera2
from PIL import Image, ImageDraw, ImageFont
# from zbarlight import scan_codes
import adafruit_rgb_display.st7789
from gpiozero import RotaryEncoder, Button
import neopixel

# --- Settings Framework ---
@dataclass
class Setting:
    name: str
    value: float
    min_val: float
    max_val: float
    step: float
    callback: Callable
    json_path: str

class SettingsManager:
    def __init__(self, settings: list[Setting], config_file: str = "scanner_config.json"):
        self.settings = settings
        self.config_file = Path(config_file)
        self.load_settings()
        # Ensure NeoPixel settings are applied on startup
        for setting in self.settings:
            setting.callback(setting.value)

    def load_settings(self):
        """Load settings from JSON file if it exists."""
        if self.config_file.exists():
            try:
                with self.config_file.open("r") as f:
                    data = json.load(f)
                    for setting in self.settings:
                        if setting.json_path in data:
                            setting.value = max(setting.min_val, min(setting.max_val, data[setting.json_path]))
                            setting.callback(setting.value)
            except json.JSONDecodeError:
                print("Error loading config, using defaults")
        else:
            # Apply default settings explicitly
            for setting in self.settings:
                setting.callback(setting.value)

    def save_settings(self):
        """Save settings to JSON file."""
        data = {setting.json_path: setting.value for setting in self.settings}
        with self.config_file.open("w") as f:
            json.dump(data, f, indent=4)

    def update_setting(self, index: int, delta: float):
        """Update a setting value and trigger its callback."""
        setting = self.settings[index]
        new_value = max(setting.min_val, min(setting.max_val, setting.value + delta * setting.step))
        setting.value = new_value
        setting.callback(new_value)
        self.save_settings()

# --- Main Scanner Application ---
class BarcodeScanner:
    def __init__(self):
        self.camera_res = (1920, 1080)
        self.preview_res = (240, 240)
        self.neopixel_rgb = [255, 255, 255]  # Initial state
        self.setup_hardware()
        self.setup_ui_state()
        self.setup_settings()
        self.last_frame_time = time.time()

    def setup_hardware(self):
        """Initialize hardware components."""
        # SPI Display (ST7789)
        spi = board.SPI()
        self.display = adafruit_rgb_display.st7789.ST7789(
            spi,
            cs=digitalio.DigitalInOut(board.CE0),
            dc=digitalio.DigitalInOut(board.D25),
            rst=digitalio.DigitalInOut(board.D24),
            width=240,
            height=240,
            rotation=180,
            baudrate=96000000,
            x_offset=0,
            y_offset=80,
        )

        # Camera
        self.picam2 = Picamera2()
        config = self.picam2.create_preview_configuration(
            main={"size": self.camera_res, "format": "RGB888"}
        )
        self.picam2.configure(config)
        self.picam2.start()

        # NeoPixel Ring
        self.pixels = neopixel.NeoPixel(board.D21, 16, brightness=0.2, auto_write=False)

        # Rotary Encoder and Button
        self.encoder = RotaryEncoder(17, 18, max_steps=0)
        self.encoder_button = Button(27, pull_up=True, bounce_time=0.1)
        self.encoder.when_rotated = self.on_encoder_change
        self.encoder_button.when_pressed = self.on_button_press
        self.encoder_button.when_released = self.on_button_release

        # Fonts (larger sizes)
        try:
            self.font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except:
            self.font = ImageFont.load_default()

    def setup_ui_state(self):
        """Initialize UI state."""
        self.mode = "SCAN"  # SCAN, SETUP_WIDTH, SETUP_HEIGHT, SETTINGS
        self.boundary_width = 400
        self.boundary_height = 300
        self.current_setting = 0
        self.menu_scroll_offset = 0  # For scrolling settings menu
        self.max_visible_settings = 5  # Number of settings visible at once
        self.encoder_pos = 0
        self.last_encoder_pos = 0
        self.button_press_time = None
        self.long_press_threshold = 1.0
        self.settings_entered_on_hold = False
        self.fps = 0
        self.fps_counter = 0
        self.fps_time = time.time()

    def setup_settings(self):
        """Define and initialize settings with callbacks."""
        def update_contrast(value: float):
            self.picam2.set_controls({"Contrast": value})

        def update_exposure(value: float):
            self.picam2.set_controls({"ExposureValue": value})

        def update_saturation(value: float):
            self.picam2.set_controls({"Saturation": value})

        def update_neopixel_brightness(value: float):
            self.pixels.brightness = value
            self.pixels.show()

        def update_neopixel_channel(index):
            def updater(value: float):
                self.neopixel_rgb[index] = int(value)
                self.pixels.fill(tuple(self.neopixel_rgb))
                self.pixels.show()
            return updater

        self.settings_manager = SettingsManager([
            Setting("Contrast", 1.0, 0.5, 2.0, 0.1, update_contrast, "contrast"),
            Setting("Exposure", 0.0, -8.0, 8.0, 0.1, update_exposure, "exposure"),
            Setting("Saturation", 1.0, 0.0, 2.0, 0.1, update_saturation, "saturation"),
            Setting("Brightness", 0.2, 0.0, 1.0, 0.05, update_neopixel_brightness, "neopixel_brightness"),
            Setting("LED Red", 255, 0, 255, 5, update_neopixel_channel(0), "neopixel_r"),
            Setting("LED Green", 255, 0, 255, 5, update_neopixel_channel(1), "neopixel_g"),
            Setting("LED Blue", 255, 0, 255, 5, update_neopixel_channel(2), "neopixel_b"),
        ])


    def color_temp_to_rgb(self, temp: float) -> Tuple[int, int, int]:
        """Convert color temperature (Kelvin) to RGB."""
        temp = max(2000, min(10000, temp)) / 100
        r = 255.0
        g = min(255.0, 99.4708 * (temp ** 0.1333))
        b = min(255.0, 55.1766 * (temp ** 0.2389))
        return (int(r), int(g), int(b))

    def on_button_press(self):
        self.button_press_time = time.time()
        self.check_long_press_active = True

        def check_long_press():
            time.sleep(self.long_press_threshold)
            if self.check_long_press_active:
                if self.mode != "SETTINGS":
                    self.mode = "SETTINGS"
                    self.settings_entered_on_hold = True  # Mark that we entered settings via hold
                    self.current_setting = 0
                    self.menu_scroll_offset = 0
                    self.last_encoder_pos = self.encoder_pos
                    print("Entering settings menu")

        import threading
        threading.Thread(target=check_long_press, daemon=True).start()


    def on_button_release(self):
        self.check_long_press_active = False
        if self.button_press_time is None:
            return
        press_duration = time.time() - self.button_press_time
        self.button_press_time = None

        if press_duration >= self.long_press_threshold:
            if self.mode == "SETTINGS" and not self.settings_entered_on_hold:
                self.mode = "SCAN"
                print("Exiting settings menu")
            self.settings_entered_on_hold = False  # reset for next time
            return  # Do not process short press actions

        # Short press behavior
        if self.mode == "SCAN":
            self.mode = "SETUP_WIDTH"
            self.last_encoder_pos = self.encoder_pos
            print("Adjusting scan width")
        elif self.mode == "SETUP_WIDTH":
            self.mode = "SETUP_HEIGHT"
            self.last_encoder_pos = self.encoder_pos
            print("Adjusting scan height")
        elif self.mode == "SETUP_HEIGHT":
            self.mode = "SCAN"
            print(f"Boundary set: {self.boundary_width}x{self.boundary_height}")
        elif self.mode == "SETTINGS":
            self.current_setting = (self.current_setting + 1) % len(self.settings_manager.settings)
            if self.current_setting < self.menu_scroll_offset:
                self.menu_scroll_offset = self.current_setting
            elif self.current_setting >= self.menu_scroll_offset + self.max_visible_settings:
                self.menu_scroll_offset = self.current_setting - self.max_visible_settings + 1
            self.last_encoder_pos = self.encoder_pos
            print(f"Selected setting: {self.settings_manager.settings[self.current_setting].name}")



    def on_encoder_change(self):
        """Handle rotary encoder rotation."""
        self.encoder_pos = self.encoder.steps
        delta = self.encoder_pos - self.last_encoder_pos
        self.last_encoder_pos = self.encoder_pos
        if self.mode == "SETUP_WIDTH":
            self.boundary_width = max(50, min(self.boundary_width + delta * 10, self.camera_res[0]))
        elif self.mode == "SETUP_HEIGHT":
            self.boundary_height = max(50, min(self.boundary_height + delta * 10, self.camera_res[1]))
        elif self.mode == "SETTINGS":
            self.settings_manager.update_setting(self.current_setting, delta)

    def get_boundary_rect(self) -> Tuple[int, int, int, int]:
        """Calculate centered boundary rectangle."""
        max_width = min(self.boundary_width, self.camera_res[0])
        max_height = min(self.boundary_height, self.camera_res[1])
        center_x, center_y = self.camera_res[0] // 2, self.camera_res[1] // 2
        x1 = max(0, center_x - max_width // 2)
        y1 = max(0, center_y - max_height // 2)
        x2 = min(self.camera_res[0], center_x + max_width // 2)
        y2 = min(self.camera_res[1], center_y + max_height // 2)
        return (x1, y1, x2, y2)

    def update_fps(self):
        """Update FPS counter."""
        self.fps_counter += 1
        current_time = time.time()
        if current_time - self.fps_time >= 1.0:
            self.fps = self.fps_counter
            self.fps_counter = 0
            self.fps_time = current_time
        print(f"FPS: {self.fps}")

    def draw_boundary_overlay(self, draw: ImageDraw, boundary: Tuple[int, int, int, int]):
        """Draw boundary rectangle and crosshair."""
        scale_x = self.preview_res[0] / self.camera_res[0]
        scale_y = self.preview_res[1] / self.camera_res[1]
        x1, y1, x2, y2 = boundary
        x1_s, y1_s = int(x1 * scale_x), int(y1 * scale_y)
        x2_s, y2_s = int(x2 * scale_x), int(y2 * scale_y)
        color = "yellow" if self.mode in ["SETUP_WIDTH", "SETUP_HEIGHT"] else "green"
        draw.rectangle([(x1_s, y1_s), (x2_s, y2_s)], outline=color, width=2)
        center_x, center_y = (x1_s + x2_s) // 2, (y1_s + y2_s) // 2
        crosshair_size = 10
        draw.line([(center_x - crosshair_size, center_y), (center_x + crosshair_size, center_y)], fill=color, width=1)
        draw.line([(center_x, center_y - crosshair_size), (center_x, center_y + crosshair_size)], fill=color, width=1)

    def draw_ui_info(self, draw: ImageDraw):
        """Draw UI information on display."""
        viewfinder_height = 120  # Reserve top 120px for camera viewfinder
        info_y = viewfinder_height + 20
        if self.mode == "SETTINGS":
            draw.rectangle([(0, viewfinder_height), self.preview_res], fill="black", outline="white", width=2)
            visible_settings = self.settings_manager.settings[self.menu_scroll_offset:self.menu_scroll_offset + self.max_visible_settings]
            for i, setting in enumerate(visible_settings):
                setting_index = self.menu_scroll_offset + i
                color = "yellow" if setting_index == self.current_setting else "white"
                unit = " EV" if setting.name == "Exposure" else "K" if setting.name == "NeoPixel Color Temp" else ""
                value = f"{setting.value:.1f}" if setting.name != "NeoPixel Brightness" else f"{setting.value:.2f}"
                draw.text((5, info_y + 18 * i), f"{setting.name}: {value}{unit}", font=self.font, fill=color)

    def run(self):
        """Main loop for barcode scanning."""
        print("Enhanced Barcode Scanner with Settings Framework")
        print("Controls: Short press: Setup boundary | Long press: Settings | Rotate: Adjust")
        print(f"Initial boundary: {self.boundary_width}x{self.boundary_height} (centered)")
        composed = Image.new("RGB", self.preview_res, (0, 0, 0))
        try:
            while True:
                frame = self.picam2.capture_array()
                image = Image.fromarray(frame)
                boundary = self.get_boundary_rect()
                cropped_image = image.crop(boundary)
                #barcodes = decode(cropped_image, symbols=[ZBarSymbol.QRCODE, ZBarSymbol.CODE128])
                barcodes = scan_codes(['qrcode', 'code128'], cropped_image)
                scaled_image = image.resize(self.preview_res, Image.Resampling.NEAREST)
                draw = ImageDraw.Draw(scaled_image)
                scale_x, scale_y = self.preview_res[0] / self.camera_res[0], self.preview_res[1] / self.camera_res[1]
                if barcodes:
                    for barcode in barcodes:
                        data = barcode.data.decode("utf-8")
                        x, y, w, h = barcode.rect
                        x += boundary[0]
                        y += boundary[1]
                        x_s, y_s = int(x * scale_x), int(y * scale_y)
                        w_s, h_s = int(w * scale_x), int(h * scale_y)
                        draw.rectangle([(x_s, y_s), (x_s + w_s, y_s + h_s)], outline="red", width=2)
                        draw.text((x_s, max(0, y_s - 12)), f"{barcode.type}: {data}", font=self.font, fill="red")
                        print(f"Detected {barcode.type}: {data}")
                if self.mode != "SETTINGS":
                    self.draw_boundary_overlay(draw, boundary)
                self.draw_ui_info(draw)
                composed.paste(scaled_image, (0, 0))
                self.display.image(composed)
                self.update_fps()
        except KeyboardInterrupt:
            print("Exiting...")
            self.pixels.fill((0, 0, 0))
            self.pixels.show()
            self.picam2.stop()

if __name__ == "__main__":
    scanner = BarcodeScanner()
    scanner.run()

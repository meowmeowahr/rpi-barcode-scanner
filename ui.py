from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING

from PIL import Image
from PIL.ImageDraw import Draw
from PIL.ImageFont import truetype
from loguru import logger

from settings import (
    AbstractSetting,
    ButtonMenuSetting,
    FloatSetting,
    GroupSetting,
    IntSetting,
    StringOptionSetting,
    get_visible_menu_items,
)
from state import UIState

if TYPE_CHECKING:
    from guifw import ScannerGui


@dataclass
class FontConfig:
    toolbar_font_name: str
    toolbar_font_size: int
    regular_font_name: str
    regular_font_size: int


@dataclass
class DisplayInfo:
    width: int
    height: int


@dataclass
class UiParams:
    toolbar_height: int
    target_width: int
    target_height: int
    visible_settings: int


@dataclass
class ConnectionData:
    udc_connected: bool


class UserInterface:
    def __init__(
        self, gui: "ScannerGui", font_config: FontConfig, display_info: DisplayInfo
    ):
        self.app = gui
        self.font_config = font_config
        self.display = display_info

        self.tb_font = truetype(
            self.font_config.toolbar_font_name, self.font_config.toolbar_font_size
        )
        self.reg_font = truetype(
            self.font_config.regular_font_name, self.font_config.regular_font_size
        )

        self.settings_stack: list[GroupSetting] = []
        self.settings_index = 0
        self.active_setting: AbstractSetting | None = None

        self.image_lock = threading.Lock()

    def visible_settings(self, settings: list[AbstractSetting]):
        if not self.settings_stack:
            return settings
        return [self.make_exit_setting()] + self.settings_stack[-1].children

    def make_exit_setting(self):
        return StringOptionSetting(
            id="exit",
            name="← Exit",
            default_value="",
            value="",
            apply_callback=lambda v: None,
            options=[""],
        )

    def draw(
        self,
        img: Image.Image,
        settings: list[AbstractSetting],
        connection: ConnectionData,
        ui_params: UiParams,
        state: UIState,
        settings_lock: threading.Lock,
    ) -> Image.Image:
        # Create a copy of the image
        draw = Draw(img)

        # draw toolbar
        draw.rectangle(
            (0, 0, self.display.width, ui_params.toolbar_height), fill="black"
        )
        # Center toolbar text vertically
        state_text = f"State: {state.value}"
        text_bbox = self.tb_font.getbbox(state_text)
        text_height = text_bbox[3] - text_bbox[1]
        y = (ui_params.toolbar_height - text_height) // 2
        draw.text((10, y), state_text, font=self.tb_font, fill="white")

        # draw right-aligned UDC text
        conn = next((s for s in settings if s.id == "connection"), None)
        if conn and conn.value == "USB":
            conn_text = f"Conn: {'OK' if connection.udc_connected else 'NO'}"
            text_bbox = self.tb_font.getbbox(conn_text)
            draw.text(
                (
                    self.display.width - text_bbox[2] - 10,
                    (ui_params.toolbar_height - text_bbox[3] + text_bbox[1]) // 2,
                ),
                conn_text,
                font=self.tb_font,
                fill="green" if connection.udc_connected else "red",
            )

        if state in [
            UIState.IDLE,
            UIState.SCAN,
            UIState.TARGET_ADJUST_W,
            UIState.TARGET_ADJUST_H,
        ]:
            # Draw centered target rectangle
            x0 = (self.display.width - ui_params.target_width) // 2
            y0 = (
                ui_params.toolbar_height
                + (
                    (self.display.height - ui_params.toolbar_height)
                    - ui_params.target_height
                )
                // 2
            )
            x1 = x0 + ui_params.target_width
            y1 = y0 + ui_params.target_height

            match state:
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
                    x0 + ui_params.target_width // 2,
                    y0,
                    x0 + ui_params.target_width // 2,
                    y1,
                ),
                fill=color,
                width=1,
            )
            draw.line(
                (
                    x0,
                    y0 + ui_params.target_height // 2,
                    x1,
                    y0 + ui_params.target_height // 2,
                ),
                fill=color,
                width=1,
            )
        elif state == UIState.SETTINGS:
            visible_count = ui_params.visible_settings
            with settings_lock:
                visible_settings = get_visible_menu_items(
                    self.visible_settings(settings),
                    self.settings_index,
                    visible_count=visible_count,
                )
                total = len(self.visible_settings(settings))

            # Dynamically calculate overlay area based on visible_count
            min_overlay_height = self.display.height // 2.8
            max_overlay_height = int(self.display.height * 0.6)
            overlay_height = max(
                min_overlay_height,
                min(
                    max_overlay_height,
                    int(self.display.height * 0.12 * visible_count),
                ),
            )
            overlay_top = self.display.height - overlay_height
            overlay_bottom = self.display.height

            overlay = Image.new(
                "RGBA", (self.display.width, self.display.height), (0, 0, 0, 0)
            )
            draw_overlay = Draw(overlay)
            draw_overlay.rectangle(
                (0, overlay_top, self.display.width, overlay_bottom),
                fill=(0, 0, 0, 200),
            )

            # Calculate item height and spacing
            item_height = overlay_height // visible_count
            vertical_padding = max(8, item_height // 8)
            text_y_offset = vertical_padding

            # Draw setting text
            for draw_index, (i, setting) in enumerate(visible_settings):
                color = "white"
                if i == self.settings_index:
                    if setting == self.active_setting:
                        color = "cyan"
                    else:
                        color = "yellow"
                if setting.id == "exit":
                    state_text = setting.name
                elif isinstance(setting, GroupSetting):
                    state_text = f"{setting.name}"
                elif isinstance(setting, FloatSetting):
                    state_text = f"{setting.name}: {round(setting.value, setting.precision)}{setting.suffix}"
                elif isinstance(setting, IntSetting):
                    state_text = f"{setting.name}: {setting.value}{setting.suffix}"
                elif isinstance(setting, StringOptionSetting):
                    state_text = f"{setting.name}: {setting.value}"
                elif isinstance(setting, ButtonMenuSetting):
                    state_text = f"<{setting.name}>"
                else:
                    logger.error(f"Unrecognized setting, {setting}")
                    state_text = "ERROR"

                y_pos = overlay_top + (text_y_offset // 2) + draw_index * item_height
                draw_overlay.text(
                    (16, y_pos),
                    state_text,
                    font=self.reg_font,
                    fill=color,
                )

            # Draw scroll indicator if needed
            if total > visible_count:
                scrollbar_left = self.display.width - 18
                scrollbar_right = self.display.width - 8
                scrollbar_top = overlay_top + 8
                scrollbar_bottom = overlay_bottom - 8
                scrollbar_height = scrollbar_bottom - scrollbar_top

                track_height = scrollbar_height
                thumb_height = max(10, int(track_height * (visible_count / total)))

                max_scroll = total - visible_count
                scroll_pos = min(
                    max(self.settings_index - (visible_count // 2), 0),
                    max_scroll,
                )
                scroll_ratio = scroll_pos / max_scroll if max_scroll > 0 else 0
                thumb_top = scrollbar_top + int(
                    (track_height - thumb_height) * scroll_ratio
                )
                thumb_bottom = thumb_top + thumb_height

                draw_overlay.rectangle(
                    (scrollbar_left, thumb_top, scrollbar_right, thumb_bottom),
                    fill="gray",
                )

            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        return img

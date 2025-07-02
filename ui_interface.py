import time
from typing import TYPE_CHECKING

from gpiozero import RotaryEncoder, Button
from loguru import logger

from settings import (
    ButtonMenuSetting,
    FloatSetting,
    GroupSetting,
    IntSetting,
    StringOptionSetting,
    AbstractSetting,
)
from state import UIState
from ui import UserInterface

if TYPE_CHECKING:
    from main import ScannerGui


class UserInterfaceInputController:
    def __init__(
        self,
        ui: UserInterface,
        app: "ScannerGui",
        settings: list[AbstractSetting],
        encoder: RotaryEncoder,
        button: Button,
        trigger: Button,
    ):
        self.encoder = encoder
        self.button = button
        self.trigger = trigger
        self.ui = ui
        self.app = app
        self.settings = settings

        self.encoder.when_rotated = self.on_encoder_turn
        self.button.when_activated = self.on_button_press
        self.button_press_time = None

        self.trigger.when_activated = self.on_trigger_press
        self.trigger.when_deactivated = self.on_trigger_release

    def on_encoder_turn(self, enc: RotaryEncoder):
        """Handle encoder rotation."""
        delta = 1 if enc.value > 0 else -1
        logger.trace(f"Encoder turned: delta={delta}, raw={enc.value}")
        enc.value = 0
        with self.ui.image_lock:
            if self.app.state == UIState.TARGET_ADJUST_W:
                width = max(10, min(200, self.app.target_width + delta * 5))
                for setting in self.settings:
                    if setting.id == "tgt_width":
                        setting.value = width
                        setting.apply()
                        self.app.target_width = width
                        self.app.save_settings()
                        break
                logger.debug(f"Target width adjusted to {self.app.target_width}")
            elif self.app.state == UIState.TARGET_ADJUST_H:
                height = max(10, min(200, self.app.target_height + delta * 5))
                for setting in self.settings:
                    if setting.id == "tgt_height":
                        setting.value = height
                        setting.apply()
                        self.app.target_height = height
                        self.app.save_settings()
                        break
                logger.debug(f"Target height adjusted to {self.app.target_height}")
            elif self.app.state == UIState.SETTINGS:
                visible = self.ui.visible_settings(self.settings)

                if self.ui.active_setting is None:
                    # Not editing – scroll cursor
                    self.ui.settings_index = max(
                        0, min(len(visible) - 1, self.ui.settings_index + delta)
                    )
                else:
                    # Editing active setting – change value
                    s = self.ui.active_setting
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
                    elif isinstance(s, ButtonMenuSetting):
                        return  # don't apply
                    s.apply()
                    self.app.save_settings()

    def on_button_press(self):
        """Handle button press (start timing for short/long detection)."""
        self.button_press_time = time.time()
        logger.debug("Button pressed")

    def handle_button(self):
        """Handle encoder button short/long press."""
        if self.button_press_time:
            now = time.time()
            short_press = (now - self.button_press_time) < self.button.hold_time
            long_press = (
                now - self.button_press_time
            ) > self.button.hold_time and self.button.is_held

            if short_press and not self.button.is_active:
                # Short press
                if self.app.state == UIState.SETTINGS:
                    visible = self.ui.visible_settings(self.settings)
                    selected = visible[self.ui.settings_index]

                    if self.ui.active_setting:
                        # Deselect setting
                        self.ui.active_setting = None
                    elif selected.id == "exit":
                        if self.ui.settings_stack:
                            self.ui.settings_stack.pop()
                            self.ui.settings_index = 0
                        else:
                            self.app.state = UIState.IDLE
                            self.ui.settings_stack.clear()
                            self.ui.settings_index = 0
                    elif isinstance(selected, GroupSetting):
                        self.ui.settings_stack.append(selected)
                        self.ui.settings_index = 0
                    else:
                        self.ui.active_setting = selected
                        if isinstance(selected, ButtonMenuSetting):
                            selected.apply()
                elif self.app.state == UIState.IDLE:
                    self.app.state = UIState.TARGET_ADJUST_W
                elif self.app.state == UIState.TARGET_ADJUST_W:
                    self.app.state = UIState.TARGET_ADJUST_H
                elif self.app.state == UIState.TARGET_ADJUST_H:
                    self.app.state = UIState.IDLE

                logger.info(f"State changed to {self.app.state}")
                self.button_press_time = None

            elif long_press:
                logger.debug("Long press detected")
                with self.ui.image_lock:
                    if self.app.state == UIState.IDLE:
                        self.app.state = UIState.SETTINGS
                        self.ui.settings_stack.clear()
                        self.settings_index = 0
                        self.active_setting = None
                    elif self.app.state == UIState.SETTINGS:
                        if self.ui.settings_stack:
                            self.ui.settings_stack.pop()  # Exit submenu
                            self.settings_index = 0
                            self.active_setting = None
                        else:
                            self.app.state = UIState.IDLE
                            self.active_setting = None

                    logger.info(f"State changed to {self.app.state}")
                self.button_press_time = None

    def on_trigger_press(self):
        logger.debug("Trigger pressed")
        if self.app.state == UIState.IDLE:
            self.app.state = UIState.SCAN
        elif self.app.state == UIState.SETTINGS:
            self.app.state = UIState.IDLE

    def on_trigger_release(self):
        logger.debug("Trigger released")
        if self.app.state == UIState.SCAN:
            self.app.state = UIState.IDLE

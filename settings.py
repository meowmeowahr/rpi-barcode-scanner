from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class AbstractSetting(ABC):
    id: str
    name: str
    default_value: Any
    value: Any
    apply_callback: Callable

    def apply(self):
        """Apply the setting by calling its callback."""
        logger.debug(f"Applying setting {self.id}: {self.value}")
        self.apply_callback(self.value)

    def to_dict(self):
        """Convert to dict for JSON serialization, excluding callback."""
        d = {"id": self.id, "value": self.value}
        return d

    @classmethod
    def from_dict(cls, data, apply_callback):
        """Create Setting from dict, reattaching callback."""
        data["apply_callback"] = apply_callback
        return cls(**data)


@dataclass
class FloatSetting(AbstractSetting):
    id: str
    name: str
    min_value: float
    max_value: float
    default_value: float
    value: float
    apply_callback: Callable
    precision: int = 1
    step: float = 0.1
    suffix: str = ""


@dataclass
class IntSetting(AbstractSetting):
    id: str
    name: str
    min_value: int
    max_value: int
    default_value: int
    value: int
    apply_callback: Callable
    step: int = 5
    suffix: str = ""


@dataclass
class StringOptionSetting(AbstractSetting):
    id: str
    name: str
    options: list[str]
    default_value: str
    value: str
    apply_callback: Callable[[str], None]
    suffix: str = ""

@dataclass
class ButtonMenuSetting(AbstractSetting):
    id: str
    name: str
    default_value: None
    value: None
    apply_callback: Callable[[], Any]

    def apply(self):
        """Apply the setting by calling its callback."""
        logger.debug(f"Applying setting {self.id}: {self.value}")
        self.apply_callback()

@dataclass
class GroupSetting(AbstractSetting):
    id: str
    name: str
    children: list[AbstractSetting]

    def apply(self):
        for child in self.children:
            child.apply()

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "children": [child.to_dict() for child in self.children],
        }

def get_visible_menu_items(menu_list, current_index, visible_count=2):
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
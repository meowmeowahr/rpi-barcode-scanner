from enum import Enum


class UIState(Enum):
    IDLE = "IDLE"
    SCAN = "SCAN"
    TARGET_ADJUST_W = "TGT-W"
    TARGET_ADJUST_H = "TGT-H"
    SETTINGS = "SETTINGS"
    NULL = "NULL"

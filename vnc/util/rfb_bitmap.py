import numpy as np
from PIL import Image
from vnc.util import bgr233_palette
from loguru import logger

__all__ = ["RfbBitmap"]


class RfbBitmap:
    def __init__(self):
        self.bpp: int | None = None
        self.depth: int | None = None
        self.truecolor: int | None = None
        self.primaryOrder: str = "rgb"
        self.dither: bool = False
        self.red_shift: int | None = None
        self.green_shift: int | None = None
        self.blue_shift: int | None = None
        self.bigendian: int = 0

    def get_bitmap(self, rectangle):
        if self.bpp is None:
            logger.error("BPP is not set")
            return None
        elif self.depth is None:
            logger.error("Depth is not set")
            return None
        elif self.red_shift is None:
            logger.error("Red shift is not set")
            return None
        elif self.green_shift is None:
            logger.error("Green shift is not set")
            return None
        elif self.blue_shift is None:
            logger.error("Blue shift is not set")
            return None

        if self.bpp == 32:
            redBits = 8
            greenBits = 8
            blueBits = 8

            # image array
            a = np.asarray(rectangle).copy()

            redMask = ((1 << redBits) - 1) << self.red_shift
            greenMask = ((1 << greenBits) - 1) << self.green_shift
            blueMask = ((1 << blueBits) - 1) << self.blue_shift
            a[..., 0] = (a[..., 0]) & redMask >> self.red_shift
            a[..., 1] = (a[..., 1]) & greenMask >> self.green_shift
            a[..., 2] = (a[..., 2]) & blueMask >> self.blue_shift

            image = Image.fromarray(a)
            if image.mode == "RGBA":
                (r, g, b, a) = image.split()
                image = Image.merge("RGB", (r, g, b))
                del r, g, b, a

            if self.primaryOrder == "rgb":
                (b, g, r) = image.split()
                image = Image.merge("RGB", (r, g, b))
                del b, g, r
            image = image.convert("RGBX")
            return image

        elif self.bpp == 16:
            # BGR565
            a = np.array(rectangle)
            r = (a[..., 0] >> 3) & 0x1F
            g = (a[..., 1] >> 2) & 0x3F
            b = (a[..., 2] >> 3) & 0x1F
            bgr565 = (r << 11) | (g << 5) | b
            bgr565 = bgr565.astype("uint16")
            if self.bigendian == 0:
                bgr565 = bgr565.byteswap().newbyteorder()
            bgr565_bytes = bgr565.tobytes()
            image = Image.frombytes(
                "RGB", rectangle.size, bgr565_bytes, "raw", "BGR;16"
            )
            return image

        elif self.bpp == 8:
            # BGR233
            image = rectangle.convert("RGB")
            a = np.array(image)
            r = (a[..., 0] >> 6) & 0x03
            g = (a[..., 1] >> 5) & 0x07
            b = (a[..., 2] >> 6) & 0x03
            bgr233 = (b << 6) | (g << 3) | r
            image = Image.fromarray(bgr233.astype("uint8"), "P")
            image.putpalette(bgr233_palette.palette)
            return image

        else:
            logger.error(f"Unsupported BPP: {self.bpp}")
            return None

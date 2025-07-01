from loguru import logger


class Error(Exception):
    pass


class WriteError(Error):
    pass


def write_to_hid_interface_immediately(hid_dev, buffer):
    try:
        hid_dev.seek(0)
        hid_dev.write(bytearray(buffer))
        hid_dev.flush()
    except BlockingIOError:
        logger.error(
            f"Failed to write to HID interface: {hid_dev}. Is USB cable connected and Gadget module installed? check https://git.io/J1T7Q"
        )

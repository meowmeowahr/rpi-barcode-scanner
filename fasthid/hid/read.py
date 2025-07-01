import os
from loguru import logger

def read_udc_gadget_suspended(udc_addr: str) -> bool:
    """
    Check if the UDC Gadget (USB Device Controller) is suspended.
    
    :param udc_addr: The address of the UDC.
    :return: True if the UDC is suspended, False otherwise.
    """
    try:
        with open(os.path.join("/sys/class/udc/", udc_addr, "gadget/suspended"), "r") as f:
            status = f.read().strip()
        return status == "1"
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.error(f"Error reading UDC gadget suspended status: {e}")
        return False
    

import queue
import threading
import time
from typing import Callable

from loguru import logger
from fasthid.hid.read import read_udc_gadget_suspended
from fasthid.keyboard import Keyboard


class HIDInterface:
    def __init__(self, hid_udc: str, hid_path: str, check_enabled: Callable[[], bool]) -> None:
        self.hid_path = hid_path
        self.check_enabled = check_enabled

        self.udc_connected = False
        self.hid_delay = 0.0
        self.barcode_queue = queue.Queue()

        self.hid_thread = threading.Thread(
            target=self.hid_sender_thread, args=(self.barcode_queue,)
        )
        self.hid_thread.start()
        
        self.hid_conn_check_thread = threading.Thread(
            target=self.hid_connection_check, args=(hid_udc,)
        )
        self.hid_conn_check_thread.start()
        
    def hid_sender_thread(self, in_queue: queue.Queue):
        kb = Keyboard(self.hid_path)
        while True:
            try:
                code = in_queue.get(timeout=0.1)
                connection = self.check_enabled()
                if connection:
                    logger.debug(f"Sending barcode over HID: {code}")
                    kb.type(code, self.hid_delay)
            except queue.Empty:
                continue

    def hid_connection_check(self, udc_path: str):
        while True:
            self.udc_connected = not read_udc_gadget_suspended(udc_path)
            time.sleep(0.5)

    def apply_delay(self, delay: float):
        self.hid_delay = delay

    def send(self, data: str):
        self.barcode_queue.put_nowait(data)
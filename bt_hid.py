import asyncio
import json
import pkgutil
import threading
from typing import Callable

from loguru import logger

from bluez_peripheral.gatt.service import Service, ServiceCollection
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags
from bluez_peripheral.gatt.descriptor import DescriptorFlags
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.agent import NoIoAgent
from bluez_peripheral import util as bluez_util

from fasthid.hid.keycodes import KeyCodes

KEYBOARD_APPEARANCE = 961

KEYBOARD_REPORT_MAP = bytes(
    [
        0x05,
        0x01,
        0x09,
        0x06,
        0xA1,
        0x01,
        0x05,
        0x07,
        0x19,
        0xE0,
        0x29,
        0xE7,
        0x15,
        0x00,
        0x25,
        0x01,
        0x75,
        0x01,
        0x95,
        0x08,
        0x81,
        0x02,
        0x95,
        0x01,
        0x75,
        0x08,
        0x81,
        0x01,
        0x95,
        0x05,
        0x75,
        0x01,
        0x05,
        0x08,
        0x19,
        0x01,
        0x29,
        0x05,
        0x91,
        0x02,
        0x95,
        0x01,
        0x75,
        0x03,
        0x91,
        0x01,
        0x95,
        0x06,
        0x75,
        0x08,
        0x15,
        0x00,
        0x25,
        0xFF,
        0x05,
        0x07,
        0x19,
        0x00,
        0x29,
        0xFF,
        0x81,
        0x00,
        0xC0,
    ]
)


class HidService(Service):
    def __init__(self):
        super().__init__("1812", primary=True)
        self._protocol_mode = bytearray([0x01])
        self._report_value = bytearray(8)
        self._boot_value = bytearray(8)

    @characteristic(
        "2A4E",
        CharacteristicFlags.READ | CharacteristicFlags.WRITE_WITHOUT_RESPONSE,
    )
    def protocol_mode(self, options):
        return bytes(self._protocol_mode)

    @protocol_mode.setter
    def protocol_mode(self, value, options):
        self._protocol_mode = bytearray(value)

    @characteristic("2A4B", CharacteristicFlags.READ)
    def report_map(self, options):
        return KEYBOARD_REPORT_MAP

    @characteristic("2A4A", CharacteristicFlags.READ)
    def hid_info(self, options):
        return bytes([0x01, 0x10, 0x00, 0x01])

    @characteristic("2A4C", CharacteristicFlags.WRITE_WITHOUT_RESPONSE)
    def control_point(self, options):
        return b""

    @control_point.setter
    def control_point(self, value, options):
        pass

    @characteristic("2A4D", CharacteristicFlags.READ | CharacteristicFlags.NOTIFY)
    def report(self, options):
        return bytes(self._report_value)

    @report.descriptor("2908", DescriptorFlags.READ)
    def report_reference(self, options):
        return bytes([0x00, 0x01])

    @characteristic("2A22", CharacteristicFlags.READ | CharacteristicFlags.NOTIFY)
    def boot_input(self, options):
        return bytes(self._boot_value)

    @boot_input.descriptor("2908", DescriptorFlags.READ)
    def boot_input_reference(self, options):
        return bytes([0x00, 0x01])


class DeviceInformationService(Service):
    def __init__(self):
        super().__init__("180A", primary=True)

    @characteristic("2A29", CharacteristicFlags.READ)
    def manufacturer_name(self, options):
        return b"Raspberry Pi"

    @characteristic("2A24", CharacteristicFlags.READ)
    def model_number(self, options):
        return b"Barcode Scanner"

    @characteristic("2A26", CharacteristicFlags.READ)
    def firmware_revision(self, options):
        return b"0.1.0"


class BluetoothHIDInterface:
    def __init__(
        self,
        name: str = "Raspberry Pi Barcode Scanner",
        check_enabled: Callable[[], bool] | None = None,
    ):
        self.name = name
        self.check_enabled = check_enabled

        self.connected = False
        self.hid_delay = 0.0
        self.ending = "\n"

        self._keymap = json.loads(
            pkgutil.get_data("fasthid", "keymaps/US.json").decode()
        )["Mapping"]

        self._running = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send_queue: asyncio.Queue | None = None
        self._pending: list[str] = []
        self._report_chr = None
        self._boot_chr = None

        self.hid_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.hid_thread.start()

    def _run_loop(self):
        try:
            asyncio.run(self._main())
        except Exception as e:
            logger.exception(f"Bluetooth HID loop stopped: {e}")

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._send_queue = asyncio.Queue()
        for data in self._pending:
            self._send_queue.put_nowait(data)
        self._pending = []
        bus = await bluez_util.get_message_bus()

        if not await bluez_util.is_bluez_available(bus):
            logger.error("BlueZ is not available, Bluetooth HID disabled")
            return

        adapter = await bluez_util.Adapter.get_first(bus)
        if not await adapter.get_powered():
            logger.info("Powering on Bluetooth adapter")
            await adapter.set_powered(True)
        await adapter.set_alias(self.name)

        agent = NoIoAgent()
        await agent.register(bus)

        hid_service = HidService()
        self._report_chr = hid_service.report
        self._boot_chr = hid_service.boot_input

        collection = ServiceCollection([hid_service, DeviceInformationService()])
        await collection.register(bus, adapter=adapter)

        advert = Advertisement(
            localName=self.name,
            serviceUUIDs=["1812"],
            appearance=KEYBOARD_APPEARANCE,
            timeout=0,
        )
        await advert.register(bus, adapter=adapter)

        sender_task = asyncio.create_task(self._sender_task())
        logger.info(f"Bluetooth HID keyboard '{self.name}' is now advertising")

        try:
            while self._running:
                try:
                    self.connected = await self._check_connections(bus)
                except Exception as e:
                    logger.trace(f"Failed to query Bluetooth connections: {e}")
                await asyncio.sleep(1)
        finally:
            sender_task.cancel()
            await agent.unregister(bus)

    async def _check_connections(self, bus) -> bool:
        introspection = await bus.introspect("org.bluez", "/")
        proxy = bus.get_proxy_object("org.bluez", "/", introspection)
        manager = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
        objects = await manager.call_get_managed_objects()
        for interfaces in objects.values():
            device = interfaces.get("org.bluez.Device1")
            if device is None:
                continue
            connected = device.get("Connected")
            if connected is not None and connected.value:
                return True
        return False

    async def _sender_task(self):
        while True:
            data = await self._send_queue.get()
            if self.check_enabled is not None and not self.check_enabled():
                continue
            if not self.connected:
                logger.warning(
                    f"No Bluetooth client connected, dropping barcode: {data}"
                )
                continue

            logger.debug(f"Sending barcode over Bluetooth HID: {data}")
            await self._type(data + self.ending)

    async def _type(self, text: str):
        for char in text:
            entry = self._lookup(char)
            if entry is None:
                continue
            modifiers, keycodes = entry
            if not keycodes:
                continue

            report = bytearray(8)
            report[0] = modifiers
            report[2] = keycodes[0]
            await self._send_report(bytes(report))
            await self._send_report(bytes(8))
            if self.hid_delay:
                await asyncio.sleep(self.hid_delay)

    def _lookup(self, char: str):
        mapping = self._keymap.get(char)
        if not mapping:
            logger.warning(f"No keymap entry for {repr(char)}")
            return None
        key_map = mapping[0]
        modifiers = 0
        for mod in key_map.get("Modifiers", []):
            modifiers |= getattr(KeyCodes, mod)
        keycodes = [getattr(KeyCodes, key) for key in key_map.get("Keys", [])]
        return modifiers, keycodes

    async def _send_report(self, report: bytes):
        for char in (self._report_chr, self._boot_chr):
            if char is not None:
                char.changed(report)

    def apply_delay(self, delay: float):
        self.hid_delay = delay

    def apply_ending(self, ending: str):
        match ending:
            case "RETURN":
                self.ending = "\n"
            case "TAB":
                self.ending = "\t"
            case _:
                self.ending = ""

    def send(self, data: str):
        if self._send_queue is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._send_queue.put_nowait, data)
        else:
            self._pending.append(data)

import queue
import threading
import time

from gpiozero import PWMOutputDevice
from gpiozero.pins.rpigpio import RPiGPIOFactory


class TonePlayer:
    def __init__(self, buzzer_pin: int):
        self.device = PWMOutputDevice(
            buzzer_pin,
            pin_factory=RPiGPIOFactory(),
            frequency=440,
            active_high=True,
            initial_value=0,
        )
        self.is_playing = False
        self.queue = queue.Queue()
        self.player_thread = threading.Thread(
            target=self._play_tones, daemon=True, name="TonePlayer"
        )
        self.player_thread.start()

    def _play_tones(self):
        while True:
            (tone, duration) = self.queue.get()
            self.device.frequency = tone
            self.device.value = 0.5
            self.is_playing = True
            time.sleep(duration)
            self.device.value = 0
            self.is_playing = False

    def tone(self, frequency: float, duration: float):
        self.queue.put((frequency, duration))

    def tones(self, notes: list[tuple[int, float]]):
        for note, duration in notes:
            self.queue.put((note, duration))

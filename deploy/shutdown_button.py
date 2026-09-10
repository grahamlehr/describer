#!/usr/bin/env python3
"""Six presses within 10 s on GPIO21 -> clean poweroff.

Runs under the system Python with the apt gpiozero/lgpio packages, not the
app's venv, so it keeps working whatever state the checkout is in. gpiozero is
imported inside main() so the gesture logic can be tested off the Pi.
"""

import logging
import subprocess
import time
from collections import deque

PIN = 21  # header pin 40; other switch leg to GND (pin 39)
COUNT = 6  # presses required
WINDOW = 10.0  # ...within this many seconds
DEBOUNCE = 0.05  # ignore edges closer than this
POLL = 0.02  # 50 Hz


class Gesture:
    """Counts rising edges; true once COUNT of them span WINDOW or less."""

    def __init__(self, initially_pressed: bool) -> None:
        self._presses: deque[float] = deque(maxlen=COUNT)
        self._was_pressed = initially_pressed  # a button already down isn't a press

    def sample(self, pressed: bool, now: float) -> bool:
        rising = pressed and not self._was_pressed
        self._was_pressed = pressed
        if not rising or (self._presses and now - self._presses[-1] < DEBOUNCE):
            return False
        self._presses.append(now)
        return len(self._presses) == COUNT and self._presses[-1] - self._presses[0] <= WINDOW


def main() -> None:
    from gpiozero import Button

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    button = Button(PIN, pull_up=True, bounce_time=DEBOUNCE)
    gesture = Gesture(button.is_pressed)
    logging.info("watching GPIO%d for %d presses within %.0fs", PIN, COUNT, WINDOW)
    while True:
        if gesture.sample(button.is_pressed, time.monotonic()):
            logging.warning("shutdown gesture detected; powering off")
            subprocess.run(["systemctl", "poweroff"], check=False)
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()

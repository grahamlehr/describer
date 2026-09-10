import importlib.util
from pathlib import Path

_PATH = Path(__file__).parent.parent / "deploy" / "shutdown_button.py"
_spec = importlib.util.spec_from_file_location("shutdown_button", _PATH)
shutdown_button = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shutdown_button)
Gesture = shutdown_button.Gesture


def tap(gesture: Gesture, at: float) -> bool:
    """One press and release; true if the press completed the gesture."""
    fired = gesture.sample(True, at)
    gesture.sample(False, at + 0.1)
    return fired


def test_six_presses_within_window_fire():
    gesture = Gesture(initially_pressed=False)
    results = [tap(gesture, i * 1.0) for i in range(6)]
    assert results == [False] * 5 + [True]


def test_six_presses_spread_too_wide_do_not_fire():
    gesture = Gesture(initially_pressed=False)
    assert not any(tap(gesture, i * 2.5) for i in range(6))


def test_window_slides_over_older_presses():
    gesture = Gesture(initially_pressed=False)
    tap(gesture, 0.0)  # too early to count with the rest
    assert not any(tap(gesture, 20.0 + i) for i in range(5))
    assert tap(gesture, 25.0)


def test_bounce_is_ignored():
    gesture = Gesture(initially_pressed=False)
    for i in range(4):
        tap(gesture, i * 1.0)
    assert not gesture.sample(True, 4.0)  # fifth press
    gesture.sample(False, 4.01)
    assert not gesture.sample(True, 4.03)  # contact bounce, 30 ms later
    gesture.sample(False, 4.1)
    assert tap(gesture, 5.0)  # so this is the sixth, not the seventh


def test_button_held_at_start_is_not_a_press():
    gesture = Gesture(initially_pressed=True)
    assert not gesture.sample(True, 0.0)
    gesture.sample(False, 0.1)
    results = [tap(gesture, 1.0 + i) for i in range(6)]
    assert results == [False] * 5 + [True]

import threading

from videotext.gui_controller import WorkerExit
from videotext.gui_events import (
    WorkerEventQueue,
    WorkerUiEvent,
    dispatch_worker_events,
)


def test_event_queue_preserves_message_then_exit_order():
    events = WorkerEventQueue()
    result = WorkerExit(0, False, "")

    events.put_message("job", {"type": "started"})
    events.put_exit("job", result)

    drained = events.drain()
    assert [(event.kind, event.payload) for event in drained] == [
        ("message", {"type": "started"}),
        ("exit", result),
    ]


def test_event_queue_accepts_messages_from_a_background_thread():
    events = WorkerEventQueue()
    producer_thread = None

    def produce() -> None:
        nonlocal producer_thread
        producer_thread = threading.get_ident()
        events.put_message("job", {"type": "progress", "stage": "download"})

    thread = threading.Thread(target=produce)
    thread.start()
    thread.join(timeout=2)

    assert producer_thread != threading.get_ident()
    assert events.drain() == [
        WorkerUiEvent(
            "message", "job", {"type": "progress", "stage": "download"}
        )
    ]


def test_dispatch_ignores_events_from_an_old_task():
    messages = []
    exits = []
    events = [WorkerUiEvent("message", "old", {"type": "started"})]

    dispatched = dispatch_worker_events(events, "new", messages.append, exits.append)

    assert dispatched == 0
    assert messages == []
    assert exits == []


def test_dispatch_calls_handlers_in_fifo_order():
    calls = []
    result = WorkerExit(0, False, "")
    events = [
        WorkerUiEvent("message", "job", {"type": "started"}),
        WorkerUiEvent("exit", "job", result),
    ]

    dispatched = dispatch_worker_events(
        events,
        "job",
        lambda message: calls.append(("message", message)),
        lambda exit_result: calls.append(("exit", exit_result)),
    )

    assert dispatched == 2
    assert calls == [
        ("message", {"type": "started"}),
        ("exit", result),
    ]

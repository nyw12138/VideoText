from collections.abc import Callable, Iterable
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from typing import Literal

from .gui_controller import WorkerExit


@dataclass(frozen=True, slots=True)
class WorkerUiEvent:
    kind: Literal["message", "exit"]
    task_id: str
    payload: object


class WorkerEventQueue:
    def __init__(self) -> None:
        self._queue: SimpleQueue[WorkerUiEvent] = SimpleQueue()

    def put_message(self, task_id: str, message: dict[str, object]) -> None:
        self._queue.put(WorkerUiEvent("message", task_id, message))

    def put_exit(self, task_id: str, result: WorkerExit) -> None:
        self._queue.put(WorkerUiEvent("exit", task_id, result))

    def drain(self) -> list[WorkerUiEvent]:
        events = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except Empty:
                return events


def dispatch_worker_events(
    events: Iterable[WorkerUiEvent],
    active_task_id: str | None,
    on_message: Callable[[dict[str, object]], None],
    on_exit: Callable[[WorkerExit], None],
) -> int:
    dispatched = 0
    for event in events:
        if event.task_id != active_task_id:
            continue
        if event.kind == "message":
            if not isinstance(event.payload, dict):
                raise TypeError("工作进程消息必须是字典")
            on_message(event.payload)
        else:
            if not isinstance(event.payload, WorkerExit):
                raise TypeError("工作进程退出事件无效")
            on_exit(event.payload)
        dispatched += 1
    return dispatched

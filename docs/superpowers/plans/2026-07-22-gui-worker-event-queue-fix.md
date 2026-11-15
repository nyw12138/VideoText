# VideoText GUI Worker Event Queue Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the GUI so worker progress and termination always reach Tkinter on its main thread.

**Architecture:** Worker reader threads enqueue typed events into a thread-safe FIFO without calling Tkinter. A recurring callback registered by the Tk main thread drains matching task events, updates controls, and logs failures; the controller guarantees cleanup even if a delivery callback fails.

**Tech Stack:** Python 3.11, Tkinter/ttk, `queue.SimpleQueue`, `subprocess`, `threading`, pytest, Ruff.

## Global Constraints

- Keep download, Whisper/CUDA configuration, and exported file formats unchanged.
- All Tkinter calls must run on the GUI main thread.
- Preserve worker-process isolation and cooperative cancellation.
- This directory is not a Git repository, so commit steps are recorded as unavailable checkpoints.

---

### Task 1: Thread-safe GUI event queue

**Files:**
- Create: `src/videotext/gui_events.py`
- Create: `tests/test_gui_events.py`

**Interfaces:**
- Produces: `WorkerUiEvent(kind: str, task_id: str, payload: object)`.
- Produces: `WorkerEventQueue.put_message()`, `put_exit()`, and `drain()`.

- [ ] **Step 1: Write failing ordering and stale-task tests**

```python
def test_event_queue_preserves_message_then_exit_order():
    events = WorkerEventQueue()
    events.put_message("job", {"type": "started"})
    events.put_exit("job", WorkerExit(0, False, ""))
    assert [event.kind for event in events.drain()] == ["message", "exit"]

def test_dispatch_ignores_events_from_an_old_task():
    events = [WorkerUiEvent("message", "old", {"type": "started"})]
    assert dispatch_worker_events(events, "new", list.append, list.append) == 0
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_gui_events.py -v`

Expected: FAIL because `videotext.gui_events` does not exist.

- [ ] **Step 3: Implement the minimal queue and dispatcher**

```python
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
        items = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except Empty:
                return items
```

`dispatch_worker_events()` filters on `active_task_id`, invokes the appropriate handler, and returns the number dispatched.

- [ ] **Step 4: Run focused tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_gui_events.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Checkpoint**

No commit: the workspace has no Git repository.

### Task 2: Main-thread GUI polling and truthful status

**Files:**
- Modify: `src/videotext/gui.py`
- Modify: `tests/test_gui.py`

**Interfaces:**
- Consumes: `WorkerEventQueue`, `dispatch_worker_events()`.
- Produces: `VideoTextApp._poll_worker_events()` and task-scoped enqueue callbacks.

- [ ] **Step 1: Write failing GUI behavior tests**

Add tests proving `started_status()` returns “工作进程已启动”, start status is “正在启动工作进程”, and callbacks created for `WorkerProcess.start()` enqueue events without calling a Tk object.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_gui.py tests/test_gui_events.py -v`

Expected: FAIL because the polling/status helpers do not exist.

- [ ] **Step 3: Replace background `root.after()` calls**

Initialize the event queue and register `_poll_worker_events()` from `__init__`. Start the controller with:

```python
lambda message: self.worker_events.put_message(task_id, message)
lambda result: self.worker_events.put_exit(task_id, result)
```

The main-thread poll drains events, dispatches only those matching `active_task_id`, catches unexpected handler exceptions, records `traceback.format_exc()`, cancels a still-running worker, restores controls, and reschedules itself while the window exists.

Handle `{"type": "started"}` explicitly, set “正在启动工作进程” before process launch, and clear `active_task_id` only when its exit event is handled.

- [ ] **Step 4: Run focused GUI tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_gui.py tests/test_gui_events.py tests/test_gui_controller.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Checkpoint**

No commit: the workspace has no Git repository.

### Task 3: Controller cleanup and diagnostics

**Files:**
- Modify: `src/videotext/gui_controller.py`
- Modify: `tests/test_gui_controller.py`

**Interfaces:**
- Preserves: `WorkerProcess.start(request, on_message, on_exit) -> threading.Thread`.
- Guarantees: process wait, stderr collection, task-directory cleanup, and state reset even if `on_message` raises.

- [ ] **Step 1: Write a failing callback-exception cleanup test**

```python
def test_callback_failure_does_not_skip_process_cleanup(tmp_path):
    process = FakeProcess('{"type":"started"}\n', 0)
    exits = []
    controller = WorkerProcess(popen=lambda *args, **kwargs: process)
    thread = controller.start(request(tmp_path), raising_callback, exits.append)
    thread.join(timeout=2)
    assert not controller.is_running
    assert not request(tmp_path).temp_root.exists()
    assert "callback" in exits[0].detail.lower()
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_gui_controller.py::test_callback_failure_does_not_skip_process_cleanup -v`

Expected: FAIL because the current consumer exits before cleanup.

- [ ] **Step 3: Protect the reader lifecycle with `try/finally`**

Continue draining stdout after a delivery failure, append its traceback to diagnostic detail, wait for the process, join stderr briefly, clean only `request.temp_root`, reset controller state under the lock, and finally attempt `on_exit`. Log any exit-callback exception without reviving the process.

- [ ] **Step 4: Run controller and worker tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_gui_controller.py tests/test_worker.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Checkpoint**

No commit: the workspace has no Git repository.

### Task 4: Regression and real-job verification

**Files:**
- Modify only if a failing check reveals a defect in the files above.

**Interfaces:**
- Verifies the complete existing CLI and GUI surface.

- [ ] **Step 1: Run the complete automated suite**

Run: `.venv\\Scripts\\python.exe -m pytest -q`

Expected: all runnable tests PASS.

- [ ] **Step 2: Run static and bytecode checks**

Run: `.venv\\Scripts\\python.exe -m ruff check src tests`

Run: `.venv\\Scripts\\python.exe -m compileall -q src`

Expected: both commands exit 0.

- [ ] **Step 3: Run a real worker job with captured protocol output**

Create a temporary request for `https://www.bilibili.com/video/BV18e411s71q/`, invoke `.venv\\Scripts\\python.exe -m videotext.worker --request <request.json>`, and verify ordered `started`, five progress stages, and `completed` messages plus output files. This may require network access and local GPU availability.

- [ ] **Step 4: Verify the visible GUI manually**

Launch `Start VideoText.vbs`, run the same URL, and confirm the status leaves “就绪” immediately, reaches “GPU 转写”, writes results, restores the Start button, and enables output actions. Visible GUI launch requires user approval in the desktop environment.


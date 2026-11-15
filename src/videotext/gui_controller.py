import logging
import os
import shutil
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import WorkerRequest, decode_message, write_request

MessageCallback = Callable[[dict[str, Any]], None]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerExit:
    return_code: int
    crashed: bool
    detail: str


def worker_python() -> Path:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        candidate = executable.with_name("python.exe")
        if candidate.is_file():
            return candidate
    return executable


class WorkerProcess:
    def __init__(self, popen=subprocess.Popen, cancel_timeout: float = 5.0):
        self._popen = popen
        self._cancel_timeout = cancel_timeout
        self._process = None
        self._request = None
        self._cancelled_process = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        request: WorkerRequest,
        on_message: MessageCallback,
        on_exit: Callable[[WorkerExit], None],
    ) -> threading.Thread:
        if self.is_running:
            raise RuntimeError("已有任务正在运行")
        request.temp_root.mkdir(parents=True, exist_ok=True)
        cancel_path = request.temp_root / "cancel.requested"
        cancel_path.unlink(missing_ok=True)
        request_path = request.temp_root / "request.json"
        write_request(request_path, request)
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        worker_env = os.environ.copy()
        worker_env["PYTHONUTF8"] = "1"
        worker_env["PYTHONIOENCODING"] = "utf-8"
        process = self._popen(
            [str(worker_python()), "-m", "videotext.worker", "--request", str(request_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=worker_env,
            creationflags=0,
            startupinfo=startupinfo,
        )
        with self._lock:
            self._process = process
            self._request = request
            self._cancelled_process = None

        stderr_chunks: list[str] = []

        def drain_stderr() -> None:
            if process.stderr is not None:
                for chunk in process.stderr:
                    stderr_chunks.append(chunk)

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()

        def consume() -> None:
            assert process.stdout is not None
            callback_failure = ""
            reader_failure = ""
            return_code = -1
            try:
                for line in process.stdout:
                    try:
                        message = decode_message(line)
                    except ValueError as exc:
                        message = {
                            "type": "diagnostic",
                            "message": str(exc),
                            "raw": line,
                        }
                    if callback_failure:
                        continue
                    try:
                        on_message(message)
                    except Exception:
                        callback_failure = (
                            "\n消息回调异常：\n" + traceback.format_exc()
                        )
                        LOGGER.exception("工作进程消息回调异常")
            except Exception:
                reader_failure = "\n读取工作进程输出异常：\n" + traceback.format_exc()
                LOGGER.exception("读取工作进程输出失败")
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        reader_failure += "\n终止工作进程异常：\n" + traceback.format_exc()
                        LOGGER.exception("读取失败后终止工作进程失败")
            finally:
                try:
                    return_code = process.wait()
                except Exception:
                    reader_failure += "\n等待工作进程异常：\n" + traceback.format_exc()
                    LOGGER.exception("等待工作进程退出失败")
                stderr_thread.join(timeout=1)
                detail = "".join(stderr_chunks) + callback_failure + reader_failure
                crashed = return_code not in {0, 1, 2, 3, 4, 5, 6, 7, 8}
                shutil.rmtree(request.temp_root, ignore_errors=True)
                with self._lock:
                    if self._process is process:
                        self._process = None
                        self._request = None
                        self._cancelled_process = None
                try:
                    on_exit(WorkerExit(return_code, crashed, detail))
                except Exception:
                    LOGGER.exception("工作进程退出回调异常")

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        return thread

    def cancel(self) -> None:
        with self._lock:
            process = self._process
            request = self._request
            if (
                process is None
                or request is None
                or process.poll() is not None
                or self._cancelled_process is process
            ):
                return
            self._cancelled_process = process
        try:
            (request.temp_root / "cancel.requested").touch()
        except OSError:
            LOGGER.exception("无法写入取消标记")

        def force() -> None:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

        timer = threading.Timer(self._cancel_timeout, force)
        timer.daemon = True
        timer.start()

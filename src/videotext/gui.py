import logging
import logging.handlers
import os
import shutil
import tempfile
import time
import tkinter as tk
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .errors import VideoTextError
from .gui_controller import WorkerExit, WorkerProcess
from .gui_events import WorkerEventQueue, dispatch_worker_events
from .protocol import WorkerRequest
from .settings import GuiSettings, load_settings, save_settings
from .validation import validate_url

LANGUAGE_LABELS = {"自动检测": None, "中文": "zh", "英文": "en"}
LANGUAGE_VALUES = {value: label for label, value in LANGUAGE_LABELS.items()}
STAGES = ("probe", "download", "normalize", "transcribe", "export")
STAGE_LABELS = {
    "probe": "读取媒体信息",
    "download": "下载音频",
    "normalize": "标准化音频",
    "transcribe": "GPU 转写",
    "export": "写入结果",
}

LOGGER = logging.getLogger(__name__)


def build_request(
    task_id: str, url: str, settings: GuiSettings, temp_root: Path
) -> WorkerRequest:
    return WorkerRequest(
        task_id=task_id,
        url=url.strip(),
        output_root=settings.output_root,
        temp_root=temp_root,
        model=settings.model,
        language=settings.language,
        keep_audio=settings.keep_audio,
        overwrite=settings.overwrite,
    )


def stage_status(active: str) -> dict[str, str]:
    if active == "done":
        return dict.fromkeys(STAGES, "done")
    active_index = STAGES.index(active)
    return {
        stage: "done" if index < active_index else "active" if stage == active else "pending"
        for index, stage in enumerate(STAGES)
    }


def attempt_start(start: Callable[[], object]) -> tuple[bool, str]:
    try:
        start()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def cleanup_failed_start(temp_root: Path) -> None:
    shutil.rmtree(temp_root, ignore_errors=True)


class VideoTextApp:
    def __init__(
        self,
        root: tk.Tk,
        controller: WorkerProcess,
        project_root: Path,
        local_app_data: Path,
    ):
        self.root = root
        self.controller = controller
        self.project_root = project_root
        self.app_data = local_app_data / "VideoText"
        self.settings_path = self.app_data / "settings.json"
        self.settings = load_settings(self.settings_path, project_root)
        self.started_at: float | None = None
        self.last_paths: list[Path] = []
        self.last_detail = ""
        self.terminal_message_received = False
        self.closing = False
        self.destroyed = False
        self.active_task_id: str | None = None
        self.worker_events = WorkerEventQueue()

        self.root.title("VideoText 视频转文字")
        self.root.geometry(self.settings.geometry)
        self.root.minsize(720, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.url_var = tk.StringVar()
        self.model_var = tk.StringVar(value=self.settings.model)
        self.language_var = tk.StringVar(
            value=LANGUAGE_VALUES.get(self.settings.language, "自动检测")
        )
        self.output_var = tk.StringVar(value=str(self.settings.output_root))
        self.keep_audio_var = tk.BooleanVar(value=self.settings.keep_audio)
        self.overwrite_var = tk.BooleanVar(value=self.settings.overwrite)
        self.open_output_var = tk.BooleanVar(value=self.settings.open_output)
        self.status_var = tk.StringVar(value="就绪")
        self.elapsed_var = tk.StringVar(value="已用时间：00:00")
        self.advanced_visible = tk.BooleanVar(value=False)
        self.stage_vars = {
            stage: tk.StringVar(value=f"○ {STAGE_LABELS[stage]}") for stage in STAGES
        }

        self._build_widgets()
        self.url_entry.focus_set()
        self.root.bind("<Return>", lambda event: self.start_task())
        self.root.after(100, self._poll_worker_events)

    def _build_widgets(self) -> None:
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        ttk.Label(main, text="视频网址").grid(row=0, column=0, sticky="w")
        self.url_entry = ttk.Entry(main, textvariable=self.url_var)
        self.url_entry.grid(row=1, column=0, sticky="ew", pady=(4, 10))

        buttons = ttk.Frame(main)
        buttons.grid(row=2, column=0, sticky="ew")
        self.start_button = ttk.Button(buttons, text="开始转换", command=self.start_task)
        self.start_button.pack(side="left")
        self.clear_button = ttk.Button(
            buttons, text="清空", command=lambda: self.url_var.set("")
        )
        self.clear_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(
            buttons, text="停止任务", command=self.stop_task, state="disabled"
        )
        self.stop_button.pack(side="right")

        ttk.Checkbutton(
            main,
            text="高级设置",
            variable=self.advanced_visible,
            command=self._toggle_advanced,
        ).grid(row=3, column=0, sticky="w", pady=(12, 4))
        self.advanced_frame = ttk.LabelFrame(main, text="高级设置", padding=10)
        self._build_advanced(self.advanced_frame)

        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.grid(row=5, column=0, sticky="ew", pady=(12, 4))
        status = ttk.Frame(main)
        status.grid(row=6, column=0, sticky="ew")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        ttk.Label(status, textvariable=self.elapsed_var).pack(side="right")

        stages = ttk.Frame(main)
        stages.grid(row=7, column=0, sticky="ew", pady=8)
        for stage in STAGES:
            ttk.Label(stages, textvariable=self.stage_vars[stage]).pack(
                side="left", padx=(0, 12)
            )

        self.log = tk.Text(main, height=8, state="disabled", wrap="word")
        self.log.grid(row=8, column=0, sticky="nsew")
        main.rowconfigure(8, weight=1)

        results = ttk.Frame(main)
        results.grid(row=9, column=0, sticky="ew", pady=(10, 0))
        self.open_dir_button = ttk.Button(
            results,
            text="打开输出目录",
            command=self.open_output_dir,
            state="disabled",
        )
        self.open_dir_button.pack(side="left")
        self.open_txt_button = ttk.Button(
            results, text="打开 TXT", command=self.open_txt, state="disabled"
        )
        self.open_txt_button.pack(side="left", padx=8)
        ttk.Button(results, text="复制详细信息", command=self.copy_detail).pack(side="right")

    def _build_advanced(self, frame: ttk.LabelFrame) -> None:
        ttk.Label(frame, text="模型").grid(row=0, column=0, sticky="w")
        self.model_box = ttk.Combobox(
            frame,
            textvariable=self.model_var,
            values=("large-v3-turbo", "medium"),
            state="readonly",
        )
        self.model_box.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(frame, text="语言").grid(row=0, column=2, sticky="w")
        self.language_box = ttk.Combobox(
            frame,
            textvariable=self.language_var,
            values=tuple(LANGUAGE_LABELS),
            state="readonly",
        )
        self.language_box.grid(row=0, column=3, sticky="ew", padx=8)
        ttk.Label(frame, text="输出目录").grid(row=1, column=0, sticky="w", pady=8)
        self.output_entry = ttk.Entry(frame, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8)
        self.choose_output_button = ttk.Button(
            frame, text="选择", command=self.choose_output
        )
        self.choose_output_button.grid(row=1, column=3)
        self.keep_audio_check = ttk.Checkbutton(
            frame, text="保留音频", variable=self.keep_audio_var
        )
        self.keep_audio_check.grid(row=2, column=0, sticky="w")
        self.overwrite_check = ttk.Checkbutton(
            frame, text="覆盖已有结果", variable=self.overwrite_var
        )
        self.overwrite_check.grid(row=2, column=1, sticky="w")
        self.open_output_check = ttk.Checkbutton(
            frame, text="完成后打开目录", variable=self.open_output_var
        )
        self.open_output_check.grid(row=2, column=2, sticky="w")
        frame.columnconfigure(1, weight=1)

    def _toggle_advanced(self) -> None:
        if self.advanced_visible.get():
            self.advanced_frame.grid(row=4, column=0, sticky="ew")
        else:
            self.advanced_frame.grid_remove()

    def current_settings(self) -> GuiSettings:
        return GuiSettings(
            model=self.model_var.get(),
            language=LANGUAGE_LABELS[self.language_var.get()],
            output_root=Path(self.output_var.get()).expanduser(),
            keep_audio=self.keep_audio_var.get(),
            overwrite=self.overwrite_var.get(),
            open_output=self.open_output_var.get(),
            geometry=self.root.geometry(),
        )

    def start_task(self) -> None:
        if self.controller.is_running:
            return
        try:
            url = validate_url(self.url_var.get().strip())
        except VideoTextError as exc:
            messagebox.showerror("网址无效", str(exc), parent=self.root)
            return
        settings = self.current_settings()
        saved, detail = attempt_start(lambda: save_settings(self.settings_path, settings))
        if not saved:
            self._handle_start_failure(detail, "无法保存界面设置，任务尚未启动。")
            return
        task_id = uuid.uuid4().hex
        temp_root = Path(tempfile.gettempdir()) / "videotext-gui" / task_id
        request = build_request(task_id, url, settings, temp_root)
        self.active_task_id = task_id
        self._set_running(True)
        self.status_var.set("正在启动工作进程")
        self.started_at = time.monotonic()
        self.last_paths = []
        self.last_detail = ""
        self.terminal_message_received = False
        self.closing = False
        self._reset_stages()
        started, detail = attempt_start(
            lambda: self.controller.start(
                request,
                lambda message: self.worker_events.put_message(task_id, message),
                lambda result: self.worker_events.put_exit(task_id, result),
            )
        )
        if not started:
            self.active_task_id = None
            cleanup_failed_start(request.temp_root)
            self._handle_start_failure(detail)
            return
        self._tick_elapsed()

    def _handle_start_failure(
        self, detail: str, summary: str = "无法启动转写工作进程。"
    ) -> None:
        self._set_running(False)
        self.started_at = None
        self.last_detail = detail
        self.status_var.set("启动失败")
        messagebox.showerror(
            "启动失败",
            f"{summary}\n\n{detail}",
            parent=self.root,
        )

    def handle_message(self, message: dict[str, object]) -> None:
        message_type = message.get("type")
        if message_type == "started":
            self.status_var.set("工作进程已启动")
            self._append_log(self.status_var.get())
        elif message_type == "progress":
            stage = str(message["stage"])
            self.status_var.set(str(message.get("message", STAGE_LABELS.get(stage, stage))))
            self._render_stages(stage)
            self._append_log(self.status_var.get())
        elif message_type == "completed":
            self.terminal_message_received = True
            self.last_paths = [Path(value) for value in message.get("paths", [])]
            self.status_var.set("处理完成")
            self.open_dir_button.configure(state="normal")
            if any(path.name == "transcript.txt" for path in self.last_paths):
                self.open_txt_button.configure(state="normal")
            if self.open_output_var.get():
                self.open_output_dir()
        elif message_type == "error":
            self.terminal_message_received = True
            self.last_detail = str(message.get("diagnostic", ""))
            if message.get("code") == "CancelledError":
                self.status_var.set("任务已停止")
            else:
                self.status_var.set("转换失败")
                messagebox.showerror(
                    "转换失败",
                    f"{message.get('message', '未知错误')}\n\n{message.get('hint', '')}",
                    parent=self.root,
                )
        elif message_type == "diagnostic":
            self.last_detail += "\n" + str(message.get("message", ""))

    def handle_exit(self, result: WorkerExit) -> None:
        self.active_task_id = None
        self.started_at = None
        self._set_running(False)
        self.last_detail += "\n" + result.detail
        LOGGER.info("工作进程退出：return_code=%s", result.return_code)
        if self.closing:
            save_settings(self.settings_path, self.current_settings())
            self.destroyed = True
            self.root.destroy()
            return
        if result.crashed:
            self.status_var.set("GPU 转写进程异常退出")
            messagebox.showerror(
                "转写进程异常退出",
                f"工作进程退出码：{result.return_code}\n请复制详细信息检查。",
                parent=self.root,
            )
        elif result.return_code != 0 and not self.terminal_message_received:
            self.status_var.set("工作进程异常结束")
            messagebox.showerror(
                "工作进程异常结束",
                f"退出码：{result.return_code}\n工作进程未返回错误消息，请复制详细信息检查。",
                parent=self.root,
            )

    def _poll_worker_events(self) -> None:
        try:
            dispatch_worker_events(
                self.worker_events.drain(),
                self.active_task_id,
                self.handle_message,
                self.handle_exit,
            )
        except Exception:
            detail = traceback.format_exc()
            LOGGER.exception("处理工作进程事件失败")
            self.last_detail = (self.last_detail + "\n" + detail).strip()
            self.status_var.set("界面处理工作进程消息失败")
            if self.controller.is_running:
                self.controller.cancel()
            self._set_running(False)
            messagebox.showerror(
                "界面消息处理失败",
                "工作进程消息无法显示，任务已停止。请复制详细信息检查。",
                parent=self.root,
            )
        finally:
            if not self.destroyed:
                self.root.after(100, self._poll_worker_events)

    def stop_task(self) -> None:
        if self.controller.is_running:
            self.status_var.set("正在停止任务…")
            self.controller.cancel()

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.clear_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.url_entry.configure(state="disabled" if running else "normal")
        self.model_box.configure(state="disabled" if running else "readonly")
        self.language_box.configure(state="disabled" if running else "readonly")
        self.output_entry.configure(state="disabled" if running else "normal")
        self.choose_output_button.configure(state="disabled" if running else "normal")
        option_state = "disabled" if running else "normal"
        self.keep_audio_check.configure(state=option_state)
        self.overwrite_check.configure(state=option_state)
        self.open_output_check.configure(state=option_state)
        if running:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _tick_elapsed(self) -> None:
        if not self.controller.is_running or self.started_at is None:
            return
        seconds = int(time.monotonic() - self.started_at)
        self.elapsed_var.set(f"已用时间：{seconds // 60:02}:{seconds % 60:02}")
        self.root.after(1000, self._tick_elapsed)

    def _reset_stages(self) -> None:
        for stage in STAGES:
            self.stage_vars[stage].set(f"○ {STAGE_LABELS[stage]}")

    def _render_stages(self, active: str) -> None:
        for stage, state in stage_status(active).items():
            marker = {"done": "✓", "active": "●", "pending": "○"}[state]
            self.stage_vars[stage].set(f"{marker} {STAGE_LABELS[stage]}")

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(
            initialdir=self.output_var.get(), parent=self.root
        )
        if selected:
            self.output_var.set(selected)

    def open_output_dir(self) -> None:
        directory = self.last_paths[0].parent if self.last_paths else Path(self.output_var.get())
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # type: ignore[attr-defined]

    def open_txt(self) -> None:
        for path in self.last_paths:
            if path.name == "transcript.txt" and path.is_file():
                os.startfile(path)  # type: ignore[attr-defined]
                return

    def copy_detail(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_detail or self.log.get("1.0", "end").strip())

    def on_close(self) -> None:
        if self.controller.is_running and not messagebox.askyesno(
            "任务仍在运行",
            "关闭将停止当前转换。是否继续？",
            parent=self.root,
        ):
            return
        if self.controller.is_running:
            self.closing = True
            self.status_var.set("正在停止任务并关闭…")
            self.controller.cancel()
            return
        save_settings(self.settings_path, self.current_settings())
        self.destroyed = True
        self.root.destroy()


def configure_logging(app_data: Path) -> None:
    log_dir = app_data / "VideoText" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "videotext.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    app_data = Path(os.environ.get("LOCALAPPDATA", project_root))
    configure_logging(app_data)
    root = tk.Tk()
    VideoTextApp(root, WorkerProcess(), project_root, app_data)
    root.mainloop()


if __name__ == "__main__":
    main()

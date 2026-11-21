from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import tempfile
import time
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .errors import InputError, VideoTextError
from .gui_controller import WorkerExit, WorkerProcess
from .gui_events import WorkerEventQueue, dispatch_worker_events
from .protocol import WorkerRequest
from .settings import GuiSettings, load_settings, save_settings
from .validation import validate_local_file, validate_url

LANGUAGE_LABELS = {"自动检测": None, "中文": "zh", "英文": "en"}
LANGUAGE_VALUES = {value: label for label, value in LANGUAGE_LABELS.items()}
STAGES = ("probe", "download", "normalize", "transcribe", "export")
STAGE_LABELS = {
    "probe": "读取媒体信息",
    "download": "获取音轨",
    "normalize": "标准化音频",
    "transcribe": "GPU 转写",
    "export": "写入结果",
}

LOGGER = logging.getLogger(__name__)


def build_request(
    task_id: str,
    url: str,
    settings: GuiSettings,
    temp_root: Path,
    input_type: str = "url",
    file_path: Path | None = None,
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
        input_type=input_type,
        file_path=file_path,
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
    except Exception as exc:  # noqa: BLE001 - convert startup failures into UI diagnostics
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def cleanup_failed_start(temp_root: Path) -> None:
    shutil.rmtree(temp_root, ignore_errors=True)


class ClearCheckBox(QCheckBox):
    """Checkbox with a deliberately drawn, high-contrast check mark."""

    INDICATOR_SIZE = 18
    LABEL_GAP = 5

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(
            self.INDICATOR_SIZE + self.LABEL_GAP + metrics.horizontalAdvance(self.text()) + 2,
            max(self.INDICATOR_SIZE, metrics.height()) + 4,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRect(
            1,
            (self.height() - self.INDICATOR_SIZE) // 2,
            self.INDICATOR_SIZE,
            self.INDICATOR_SIZE,
        )
        checked = self.isChecked()
        enabled = self.isEnabled()
        if checked and enabled:
            fill, border, mark = QColor("#3473d1"), QColor("#3473d1"), QColor("#ffffff")
        elif checked:
            fill, border, mark = QColor("#d9e0eb"), QColor("#c4cedd"), QColor("#ffffff")
        elif enabled:
            fill, border, mark = QColor("#ffffff"), QColor("#aebbd0"), QColor("#ffffff")
        else:
            fill, border, mark = QColor("#f1f3f7"), QColor("#d5dbe5"), QColor("#ffffff")
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
        if checked:
            path = QPainterPath()
            path.moveTo(rect.left() + 4, rect.center().y())
            path.lineTo(rect.left() + 7, rect.center().y() + 3)
            path.lineTo(rect.right() - 4, rect.center().y() - 4)
            painter.setPen(
                QPen(
                    mark,
                    2,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        text_color = QColor("#4b5669" if enabled else "#9ca5b4")
        painter.setPen(text_color)
        label_rect = QRect(
            rect.right() + self.LABEL_GAP + 1,
            0,
            max(0, self.width() - rect.right() - self.LABEL_GAP - 1),
            self.height(),
        )
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class MediaFileDropArea(QFrame):
    fileDropped = Signal(str)
    fileRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("fileDropArea")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.fileRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_file():
            self.setProperty("dragging", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.is_file():
                self.fileDropped.emit(str(path))
                event.acceptProposedAction()
                return
        event.ignore()


class VideoTextWindow(QMainWindow):
    def __init__(
        self,
        controller: WorkerProcess,
        project_root: Path,
        local_app_data: Path,
    ):
        super().__init__()
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
        self.active_task_id: str | None = None
        self.worker_events = WorkerEventQueue()
        self._destroyed = False

        self.setWindowTitle("VideoText 视频音频转写系统")
        self.setMinimumSize(780, 620)
        self.resize_from_settings(self.settings.geometry)
        self._build_ui()
        self._apply_style()
        self.url_entry.setFocus()
        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self._poll_worker_events)
        self.event_timer.start(100)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)
        self.elapsed_timer.start(1000)

    def resize_from_settings(self, geometry: str) -> None:
        try:
            width, height = (int(part) for part in geometry.lower().split("x", maxsplit=1))
        except (TypeError, ValueError):
            width, height = 900, 760
        self.resize(max(width, 760), max(height, 640))

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralSurface")
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 16, 26, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("VideoText")
        title.setObjectName("appTitle")
        subtitle = QLabel("将视频或音频转换为文本和字幕")
        subtitle.setObjectName("muted")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch(1)
        self.header_status = QLabel("本地 GPU 转写")
        self.header_status.setObjectName("badge")
        header.addWidget(self.header_status, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        input_card = QFrame()
        input_card.setObjectName("card")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(22, 20, 22, 20)
        input_layout.setSpacing(12)
        input_heading = QLabel("媒体来源")
        input_heading.setObjectName("sectionTitle")
        mode_row = QHBoxLayout()
        mode_row.addWidget(input_heading)
        mode_row.addStretch(1)
        self.input_mode_group = QButtonGroup(self)
        self.input_mode_group.setExclusive(True)
        self.online_mode_button = QToolButton()
        self.online_mode_button.setText("在线链接")
        self.online_mode_button.setObjectName("sourceModeButton")
        self.online_mode_button.setCheckable(True)
        self.online_mode_button.setChecked(True)
        self.file_mode_button = QToolButton()
        self.file_mode_button.setText("本地文件")
        self.file_mode_button.setObjectName("sourceModeButton")
        self.file_mode_button.setCheckable(True)
        self.input_mode_group.addButton(self.online_mode_button, 0)
        self.input_mode_group.addButton(self.file_mode_button, 1)
        mode_switch = QFrame()
        mode_switch.setObjectName("sourceModeSwitch")
        mode_switch_layout = QHBoxLayout(mode_switch)
        mode_switch_layout.setContentsMargins(3, 3, 3, 3)
        mode_switch_layout.setSpacing(2)
        mode_switch_layout.addWidget(self.online_mode_button)
        mode_switch_layout.addWidget(self.file_mode_button)
        mode_row.addWidget(mode_switch)
        input_layout.addLayout(mode_row)
        self.input_hint = QLabel("支持 Bilibili 和 YouTube 的公开视频")
        self.input_hint.setObjectName("muted")
        input_layout.addWidget(self.input_hint)

        self.url_input_row = QWidget()
        url_row = QHBoxLayout()
        url_row.setContentsMargins(0, 0, 0, 0)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("粘贴或输入视频网址")
        self.url_entry.setClearButtonEnabled(True)
        self.url_entry.returnPressed.connect(self.start_task)
        self.paste_button = QPushButton("粘贴")
        self.paste_button.setObjectName("secondaryButton")
        self.paste_button.clicked.connect(self.paste_url)
        url_row.addWidget(self.url_entry, 1)
        url_row.addWidget(self.paste_button)
        self.url_input_row.setLayout(url_row)
        input_layout.addWidget(self.url_input_row)

        self.selected_file_path: Path | None = None
        self.file_input_row = MediaFileDropArea()
        self.file_input_row.setMinimumHeight(132)
        self.file_input_row.fileDropped.connect(self._set_selected_file)
        self.file_input_row.fileRequested.connect(self.choose_input_file)
        file_row = QVBoxLayout(self.file_input_row)
        file_row.setContentsMargins(16, 12, 16, 12)
        file_row.setSpacing(8)
        self.file_prompt = QWidget()
        prompt_layout = QVBoxLayout(self.file_prompt)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(3)
        prompt_title = QLabel("拖放音频或视频文件到这里")
        prompt_title.setObjectName("dropTitle")
        prompt_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        prompt_hint = QLabel("或点击此处浏览选择文件")
        prompt_hint.setObjectName("muted")
        prompt_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        prompt_layout.addWidget(prompt_title)
        prompt_layout.addWidget(prompt_hint)
        file_row.addStretch(1)
        file_row.addWidget(self.file_prompt)

        self.selected_file_card = QWidget()
        selected_layout = QHBoxLayout(self.selected_file_card)
        selected_layout.setContentsMargins(8, 4, 8, 4)
        selected_layout.setSpacing(12)
        self.selected_file_icon = QLabel("▶")
        self.selected_file_icon.setObjectName("dropIcon")
        self.selected_file_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_file_icon.setFixedSize(44, 44)
        self.selected_file_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.selected_file_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        selected_layout.addWidget(self.selected_file_icon)
        selected_text = QVBoxLayout()
        selected_text.setSpacing(3)
        self.selected_file_name = QLabel("")
        self.selected_file_name.setObjectName("dropTitle")
        self.selected_file_name.setWordWrap(True)
        self.selected_file_name.setMinimumWidth(0)
        self.selected_file_name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.selected_file_detail = QLabel("")
        self.selected_file_detail.setObjectName("muted")
        self.selected_file_detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        selected_text.addWidget(self.selected_file_name)
        selected_text.addWidget(self.selected_file_detail)
        selected_layout.addLayout(selected_text, 1)
        self.replace_file_button = QPushButton("更换")
        self.replace_file_button.setObjectName("secondaryButton")
        self.replace_file_button.clicked.connect(self.choose_input_file)
        self.remove_file_button = QPushButton("移除")
        self.remove_file_button.setObjectName("textButton")
        self.remove_file_button.clicked.connect(self.remove_selected_file)
        selected_layout.addWidget(self.replace_file_button)
        selected_layout.addWidget(self.remove_file_button)
        self.selected_file_card.hide()
        file_row.addWidget(self.selected_file_card)
        file_row.addStretch(1)
        self.file_input_row.hide()
        input_layout.addWidget(self.file_input_row)
        self.input_mode_group.idClicked.connect(self._on_input_mode_changed)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_task)
        self.stop_button = QPushButton("停止任务")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_task)
        self.advanced_button = QPushButton("高级设置")
        self.advanced_button.setObjectName("secondaryButton")
        self.advanced_button.clicked.connect(self._open_advanced_settings)
        for button in (self.start_button, self.stop_button, self.advanced_button):
            button.setFixedSize(150, 44)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.stop_button)
        action_row.addWidget(self.advanced_button)
        action_row.addStretch(1)
        input_layout.addLayout(action_row)

        self.advanced_dialog = QDialog(self)
        self.advanced_dialog.setWindowTitle("VideoText 高级设置")
        self.advanced_dialog.setMinimumWidth(700)
        self.advanced_dialog.setModal(False)
        dialog_layout = QVBoxLayout(self.advanced_dialog)
        dialog_layout.setContentsMargins(20, 18, 20, 18)
        dialog_layout.setSpacing(12)
        dialog_title = QLabel("转换选项")
        dialog_title.setObjectName("sectionTitle")
        dialog_layout.addWidget(dialog_title)

        self.advanced_frame = QFrame(self.advanced_dialog)
        self.advanced_frame.setObjectName("advancedPanel")
        advanced = QVBoxLayout(self.advanced_frame)
        advanced.setContentsMargins(12, 10, 12, 10)
        advanced.setSpacing(8)
        self.model_box = QComboBox()
        self.model_box.addItems(("large-v3-turbo", "medium"))
        self.model_box.setCurrentText(self.settings.model)
        self.language_box = QComboBox()
        self.language_box.addItems(tuple(LANGUAGE_LABELS))
        self.language_box.setCurrentText(LANGUAGE_VALUES.get(self.settings.language, "自动检测"))
        self.output_entry = QLineEdit(str(self.settings.output_root))
        for control in (self.model_box, self.language_box, self.output_entry):
            control.setMinimumHeight(36)
        self.choose_output_button = QPushButton("选择…")
        self.choose_output_button.setObjectName("secondaryButton")
        self.choose_output_button.clicked.connect(self.choose_output)
        self.keep_audio_check = ClearCheckBox("保留音频文件")
        self.keep_audio_check.setChecked(self.settings.keep_audio)
        self.overwrite_check = ClearCheckBox("覆盖已有结果")
        self.overwrite_check.setChecked(self.settings.overwrite)
        self.open_output_check = ClearCheckBox("完成后打开输出目录")
        self.open_output_check.setChecked(self.settings.open_output)
        fields_row = QHBoxLayout()
        fields_row.setSpacing(10)
        model_label = QLabel("识别模型")
        model_label.setMinimumWidth(64)
        language_label = QLabel("转写语言")
        language_label.setMinimumWidth(64)
        fields_row.addWidget(model_label)
        fields_row.addWidget(self.model_box, 1)
        fields_row.addWidget(language_label)
        fields_row.addWidget(self.language_box, 1)
        advanced.addLayout(fields_row)

        output_row = QHBoxLayout()
        output_row.setSpacing(10)
        output_label = QLabel("输出目录")
        output_label.setMinimumWidth(64)
        output_row.addWidget(output_label)
        output_row.addWidget(self.output_entry, 1)
        output_row.addWidget(self.choose_output_button)
        advanced.addLayout(output_row)

        options_row = QHBoxLayout()
        options_row.setSpacing(16)
        options_row.addWidget(self.keep_audio_check)
        options_row.addWidget(self.overwrite_check)
        options_row.addWidget(self.open_output_check)
        options_row.addStretch(1)
        advanced.addLayout(options_row)
        dialog_layout.addWidget(self.advanced_frame)
        dialog_buttons = QHBoxLayout()
        dialog_buttons.addStretch(1)
        close_settings_button = QPushButton("完成")
        close_settings_button.setObjectName("primaryButton")
        close_settings_button.clicked.connect(self.advanced_dialog.accept)
        dialog_buttons.addWidget(close_settings_button)
        dialog_layout.addLayout(dialog_buttons)
        root.addWidget(input_card)

        progress_card = QFrame()
        progress_card.setObjectName("card")
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(22, 18, 22, 18)
        progress_layout.setSpacing(14)
        progress_top = QHBoxLayout()
        progress_title = QLabel("转换进度")
        progress_title.setObjectName("sectionTitle")
        self.elapsed_label = QLabel("00:00")
        self.elapsed_label.setObjectName("muted")
        progress_top.addWidget(progress_title)
        progress_top.addStretch(1)
        progress_top.addWidget(QLabel("已用时间"))
        progress_top.addWidget(self.elapsed_label)
        progress_layout.addLayout(progress_top)
        self.stage_labels: dict[str, QLabel] = {}
        stage_row = QHBoxLayout()
        stage_row.setSpacing(8)
        for index, stage in enumerate(STAGES):
            label = QLabel(f"○  {STAGE_LABELS[stage]}")
            label.setObjectName("stage")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stage_labels[stage] = label
            stage_row.addWidget(label, 1)
            if index < len(STAGES) - 1:
                separator = QLabel("›")
                separator.setObjectName("muted")
                stage_row.addWidget(separator)
        progress_layout.addLayout(stage_row)
        self.status_label = QLabel("就绪，粘贴视频链接或选择本地媒体文件后开始转换。")
        self.status_label.setObjectName("status")
        progress_layout.addWidget(self.status_label)
        root.addWidget(progress_card)

        self.content_card = QFrame()
        self.content_card.setObjectName("card")
        content_layout = QVBoxLayout(self.content_card)
        content_layout.setContentsMargins(22, 18, 22, 18)
        content_layout.setSpacing(10)
        content_header = QHBoxLayout()
        self.content_title = QLabel("任务信息")
        self.content_title.setObjectName("sectionTitle")
        self.copy_detail_button = QPushButton("复制诊断信息")
        self.copy_detail_button.setObjectName("textButton")
        self.copy_detail_button.clicked.connect(self.copy_detail)
        content_header.addWidget(self.content_title)
        content_header.addStretch(1)
        content_header.addWidget(self.copy_detail_button)
        content_layout.addLayout(content_header)
        self.idle_hint = QLabel("完成后将在这里显示转写结果位置和处理日志。\n结果包含 TXT、SRT 和 JSON 文件。")
        self.idle_hint.setObjectName("emptyHint")
        self.idle_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self.idle_hint, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("任务运行日志将显示在这里")
        self.log.setMinimumHeight(100)
        self.log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.log.hide()
        content_layout.addWidget(self.log, 1)
        root.addWidget(self.content_card, 1)

        results = QHBoxLayout()
        self.open_dir_button = QPushButton("打开输出目录")
        self.open_dir_button.setObjectName("secondaryButton")
        self.open_dir_button.setEnabled(False)
        self.open_dir_button.clicked.connect(self.open_output_dir)
        self.open_txt_button = QPushButton("打开 TXT")
        self.open_txt_button.setObjectName("secondaryButton")
        self.open_txt_button.setEnabled(False)
        self.open_txt_button.clicked.connect(self.open_txt)
        self.open_srt_button = QPushButton("打开 SRT")
        self.open_srt_button.setObjectName("secondaryButton")
        self.open_srt_button.setEnabled(False)
        self.open_srt_button.clicked.connect(self.open_srt)
        results.addWidget(self.open_dir_button)
        results.addWidget(self.open_txt_button)
        results.addWidget(self.open_srt_button)
        results.addStretch(1)
        root.addLayout(results)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("mainScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(central)
        self.setCentralWidget(self.scroll_area)

    def _apply_style(self) -> None:
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet("""
            QMainWindow, QWidget#centralSurface { background: #f4f6fa; color: #20283a; }
            QScrollArea#mainScrollArea { background: #f4f6fa; }
            QFrame#card { background: #ffffff; border: 1px solid #e5e9f1; border-radius: 14px; }
            QFrame#advancedPanel { background: #f7f9fc; border: 1px solid #e8edf5; border-radius: 10px; }
            QFrame#sourceModeSwitch { background: #f0f3f8; border-radius: 9px; }
            QToolButton#sourceModeButton { color: #647188; background: transparent; border: 0; border-radius: 7px; padding: 7px 14px; }
            QToolButton#sourceModeButton:checked { color: #2d67bf; background: #ffffff; font-weight: 650; }
            QFrame#fileDropArea { background: #f9fbfe; border: 1px dashed #c8d4e6; border-radius: 10px; }
            QFrame#fileDropArea[dragging="true"] { background: #eff5ff; border-color: #3473d1; }
            QLabel#appTitle { font-size: 23px; font-weight: 700; color: #17233b; }
            QLabel#sectionTitle { font-size: 14px; font-weight: 650; color: #263550; }
            QLabel#muted { color: #7a8497; }
            QLabel#dropTitle { color: #34435a; font-weight: 600; }
            QLabel#dropIcon { color: #3473d1; background: #eaf1ff; border-radius: 8px; font-size: 19px; font-weight: 600; }
            QLabel#badge { color: #3567bd; background: #eaf1ff; border-radius: 10px; padding: 8px 12px; }
            QLabel#status { color: #5c6678; padding-top: 2px; }
            QLabel#stage { color: #8791a2; background: #f6f7fa; border-radius: 8px; padding: 10px 6px; }
            QLabel#stage[active="true"] { color: #245bb5; background: #eaf1ff; font-weight: 650; }
            QLabel#stage[done="true"] { color: #25805b; background: #eaf7f1; }
            QLabel#emptyHint { color: #929bad; line-height: 1.5; }
            QLineEdit, QComboBox, QPlainTextEdit { background: #ffffff; border: 1px solid #dce2ec; border-radius: 8px; selection-background-color: #3974d2; }
            QLineEdit { min-height: 40px; padding: 0 12px; }
            QComboBox { min-height: 40px; padding: 0 10px; }
            QPlainTextEdit { padding: 10px 12px; }
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #6b96e2; }
            QPushButton { border: 0; border-radius: 8px; padding: 10px 16px; font-weight: 600; }
            QPushButton#primaryButton { color: white; background: #3473d1; min-width: 120px; }
            QPushButton#primaryButton:hover { background: #2864bf; }
            QPushButton#primaryButton:disabled { background: #aabbd7; }
            QPushButton#secondaryButton { color: #33425a; background: #edf1f7; }
            QPushButton#secondaryButton:hover { background: #e2e8f1; }
            QPushButton#dangerButton { color: #a53e45; background: #fff0f0; border: 1px solid transparent; }
            QPushButton#dangerButton:hover:!disabled { color: #96343b; background: #ffe1e3; border-color: #f2bfc3; }
            QPushButton#dangerButton:pressed:!disabled { color: #852b32; background: #ffd3d6; border-color: #edb0b5; }
            QPushButton#dangerButton:disabled { color: #c1a6a8; background: #f7f3f3; border-color: transparent; }
            QPushButton#textButton { color: #52627c; background: transparent; }
            QPushButton#textButton:hover { background: #edf1f7; }
            QCheckBox { spacing: 8px; color: #4b5669; }
        """)

    def _open_advanced_settings(self) -> None:
        self.advanced_dialog.show()
        self.advanced_dialog.raise_()
        self.advanced_dialog.activateWindow()

    def current_settings(self) -> GuiSettings:
        return GuiSettings(
            model=self.model_box.currentText(),
            language=LANGUAGE_LABELS[self.language_box.currentText()],
            output_root=Path(self.output_entry.text()).expanduser(),
            keep_audio=self.keep_audio_check.isChecked(),
            overwrite=self.overwrite_check.isChecked(),
            open_output=self.open_output_check.isChecked(),
            geometry=f"{self.width()}x{self.height()}",
        )

    def paste_url(self) -> None:
        clipboard_text = QApplication.clipboard().text().strip()
        if clipboard_text:
            self.url_entry.setText(clipboard_text)
            self.url_entry.setFocus()

    def _on_input_mode_changed(self, index: int) -> None:
        is_file = index == 1
        self.online_mode_button.setChecked(not is_file)
        self.file_mode_button.setChecked(is_file)
        self.url_input_row.setVisible(not is_file)
        self.file_input_row.setVisible(is_file)
        self.input_hint.setText(
            "选择本地音频或视频文件，程序会自动提取音轨并生成转写结果。"
            if is_file
            else "支持 Bilibili 和 YouTube 的公开视频"
        )

    def choose_input_file(self) -> None:
        filters = (
            "媒体文件 (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff "
            "*.aif *.amr *.ape *.caf *.mp4 *.mov *.mkv *.mka *.avi *.webm *.mpeg "
            "*.mpg *.wmv *.flv *.m4v *.3gp *.3g2 *.mts *.m2ts *.ts *.vob *.ogv);;"
            "所有文件 (*)"
        )
        start_dir = str(self.selected_file_path.parent) if self.selected_file_path else ""
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择音频或视频文件", start_dir, filters
        )
        if selected:
            self._set_selected_file(selected)

    def _set_selected_file(self, value: str) -> None:
        try:
            path = validate_local_file(Path(value))
        except (VideoTextError, OSError) as exc:
            self.status_label.setText(str(exc))
            self.file_input_row.setStyleSheet(
                "QFrame#fileDropArea { border: 1px solid #d45b62; }"
            )
            return
        self.selected_file_path = path
        size_bytes = path.stat().st_size
        if size_bytes >= 1024**3:
            size_text = f"{size_bytes / 1024**3:.1f} GB"
        elif size_bytes >= 1024**2:
            size_text = f"{size_bytes / 1024**2:.1f} MB"
        else:
            size_text = f"{max(1, size_bytes // 1024)} KB"
        self.selected_file_name.setText(path.name)
        self.selected_file_name.setToolTip(str(path))
        self.selected_file_detail.setText(f"本地媒体 · {size_text} · 点击或拖放可更换")
        self.file_prompt.hide()
        self.selected_file_card.show()
        self.file_input_row.setStyleSheet("")
        self.status_label.setText("文件已就绪，点击“开始转换”开始转写。")

    def remove_selected_file(self) -> None:
        self.selected_file_path = None
        self.selected_file_name.clear()
        self.selected_file_name.setToolTip("")
        self.selected_file_detail.clear()
        self.selected_file_card.hide()
        self.file_prompt.show()
        self.file_input_row.setStyleSheet("")
        self.status_label.setText("已移除文件，请拖放或浏览选择媒体文件。")

    def start_task(self) -> None:
        if self.controller.is_running:
            return
        input_type = "file" if self.input_mode_group.checkedId() == 1 else "url"
        file_path: Path | None = None
        if input_type == "url":
            try:
                url = validate_url(self.url_entry.text().strip())
            except VideoTextError as exc:
                self.status_label.setText(str(exc))
                self.url_entry.setFocus()
                self.url_entry.setStyleSheet("border: 1px solid #d45b62;")
                return
            self.url_entry.setStyleSheet("")
        else:
            try:
                if self.selected_file_path is None:
                    raise InputError("尚未选择文件。", "请拖放文件或点击浏览选择文件。")
                file_path = validate_local_file(self.selected_file_path)
            except (VideoTextError, OSError) as exc:
                self.status_label.setText(str(exc))
                focus_target = (
                    self.browse_file_button
                    if self.selected_file_path is None
                    else self.replace_file_button
                )
                focus_target.setFocus()
                self.file_input_row.setStyleSheet(
                    "QFrame#fileDropArea { border: 1px solid #d45b62; }"
                )
                return
            url = ""
            self.file_input_row.setStyleSheet("")
        settings = self.current_settings()
        saved, detail = attempt_start(lambda: save_settings(self.settings_path, settings))
        if not saved:
            self._handle_start_failure(detail, "无法保存界面设置，任务尚未启动。")
            return
        task_id = uuid.uuid4().hex
        temp_root = Path(tempfile.gettempdir()) / "videotext-gui" / task_id
        request = build_request(
            task_id, url, settings, temp_root, input_type=input_type, file_path=file_path
        )
        self.active_task_id = task_id
        self._set_running(True)
        self.status_label.setText("正在启动工作进程…")
        self.started_at = time.monotonic()
        self.last_paths = []
        self.last_detail = ""
        self.terminal_message_received = False
        self.closing = False
        self._reset_stages()
        self.idle_hint.hide()
        self.log.clear()
        self.log.show()
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

    def _handle_start_failure(self, detail: str, summary: str = "无法启动转写工作进程。") -> None:
        self._set_running(False)
        self.started_at = None
        self.last_detail = detail
        self.status_label.setText(summary)
        self.log.appendPlainText(f"{summary}\n{detail}")
        self.log.show()

    def handle_message(self, message: dict[str, object]) -> None:
        message_type = message.get("type")
        if message_type == "started":
            self.status_label.setText("工作进程已启动，正在准备任务。")
            self._append_log("工作进程已启动")
        elif message_type == "progress":
            stage = str(message.get("stage", ""))
            description = str(message.get("message", STAGE_LABELS.get(stage, stage)))
            self.status_label.setText(description)
            if stage in STAGES:
                self._render_stages(stage)
            self._append_log(description)
        elif message_type == "completed":
            self.terminal_message_received = True
            self.last_paths = [Path(value) for value in message.get("paths", [])]
            self.status_label.setText("转换完成")
            self._render_stages("done")
            self.open_dir_button.setEnabled(True)
            self.open_txt_button.setEnabled(any(path.name == "transcript.txt" for path in self.last_paths))
            self.open_srt_button.setEnabled(any(path.name == "transcript.srt" for path in self.last_paths))
            names = "、".join(path.name for path in self.last_paths)
            location = self.last_paths[0].parent if self.last_paths else self.output_entry.text()
            self._append_log(f"\n已生成：{names}\n保存位置：{location}")
            if self.open_output_check.isChecked():
                self.open_output_dir()
        elif message_type == "error":
            self.terminal_message_received = True
            self.last_detail = str(message.get("diagnostic", ""))
            if message.get("code") == "CancelledError":
                self.status_label.setText("任务已停止")
            else:
                summary = str(message.get("message", "未知错误"))
                hint = str(message.get("hint", ""))
                self.status_label.setText(f"转换失败：{summary}")
                self._append_log(f"{summary}\n{hint}".strip())
        elif message_type == "diagnostic":
            self.last_detail += "\n" + str(message.get("message", ""))

    def handle_exit(self, result: WorkerExit) -> None:
        self.active_task_id = None
        self.started_at = None
        self._set_running(False)
        self.last_detail = (self.last_detail + "\n" + result.detail).strip()
        LOGGER.info("工作进程退出：return_code=%s", result.return_code)
        if self.closing:
            save_settings(self.settings_path, self.current_settings())
            self.close()
            return
        if result.crashed:
            self.status_label.setText("GPU 转写进程异常退出")
            self._append_log(f"工作进程退出码：{result.return_code}。请复制诊断信息检查。")
        elif result.return_code != 0 and not self.terminal_message_received:
            self.status_label.setText("工作进程异常结束")
            self._append_log(f"退出码：{result.return_code}。工作进程未返回错误消息。")

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
            self.status_label.setText("界面处理工作进程消息失败，任务已停止。")
            if self.controller.is_running:
                self.controller.cancel()
            self._set_running(False)
            self._append_log("请复制诊断信息检查。")

    def stop_task(self) -> None:
        if self.controller.is_running:
            self.status_label.setText("正在停止任务…")
            self.controller.cancel()

    def _set_running(self, running: bool) -> None:
        for widget in (
            self.url_entry,
            self.online_mode_button,
            self.file_mode_button,
            self.paste_button,
            self.file_input_row,
            self.advanced_button,
            self.model_box,
            self.language_box,
            self.output_entry,
            self.choose_output_button,
            self.keep_audio_check,
            self.overwrite_check,
            self.open_output_check,
        ):
            widget.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if running:
            self.open_dir_button.setEnabled(False)
            self.open_txt_button.setEnabled(False)
            self.open_srt_button.setEnabled(False)

    def _tick_elapsed(self) -> None:
        if self.started_at is None:
            return
        seconds = int(time.monotonic() - self.started_at)
        self.elapsed_label.setText(f"{seconds // 60:02}:{seconds % 60:02}")

    def _reset_stages(self) -> None:
        for label in self.stage_labels.values():
            label.setText("○  " + label.text().split("  ", maxsplit=1)[-1])
            label.setProperty("active", False)
            label.setProperty("done", False)
            label.style().unpolish(label)
            label.style().polish(label)

    def _render_stages(self, active: str) -> None:
        for stage, state in stage_status(active).items():
            label = self.stage_labels[stage]
            marker = {"done": "✓", "active": "●", "pending": "○"}[state]
            label.setText(f"{marker}  {STAGE_LABELS[stage]}")
            label.setProperty("active", state == "active")
            label.setProperty("done", state == "done")
            label.style().unpolish(label)
            label.style().polish(label)

    def _append_log(self, line: str) -> None:
        self.idle_hint.hide()
        self.log.show()
        self.log.appendPlainText(line)

    def choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            self.output_entry.text(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.output_entry.setText(selected)

    def open_output_dir(self) -> None:
        directory = self.last_paths[0].parent if self.last_paths else Path(self.output_entry.text())
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # type: ignore[attr-defined]

    def _open_result(self, name: str) -> None:
        for path in self.last_paths:
            if path.name == name and path.is_file():
                os.startfile(path)  # type: ignore[attr-defined]
                return

    def open_txt(self) -> None:
        self._open_result("transcript.txt")

    def open_srt(self) -> None:
        self._open_result("transcript.srt")

    def copy_detail(self) -> None:
        detail = self.last_detail or self.log.toPlainText() or self.status_label.text()
        QApplication.clipboard().setText(detail)
        self.statusBar().showMessage("诊断信息已复制", 2500)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.controller.is_running and not self.closing:
            answer = QMessageBox.question(
                self,
                "任务仍在运行",
                "关闭将停止当前转换。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.closing = True
            self.status_label.setText("正在停止任务并关闭…")
            self.controller.cancel()
            event.ignore()
            return
        save_settings(self.settings_path, self.current_settings())
        self._destroyed = True
        self.event_timer.stop()
        self.elapsed_timer.stop()
        event.accept()


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
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("VideoText")
    app.setOrganizationName("VideoText")
    window = VideoTextWindow(WorkerProcess(), project_root, app_data)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()

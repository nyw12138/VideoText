from pathlib import Path

from PySide6.QtWidgets import QApplication

from videotext.gui import (
    LANGUAGE_LABELS,
    ClearCheckBox,
    VideoTextWindow,
    attempt_start,
    build_request,
    cleanup_failed_start,
    stage_status,
)
from videotext.gui_controller import WorkerExit
from videotext.settings import GuiSettings


def test_build_request_preserves_full_url_and_maps_language(tmp_path: Path):
    settings = GuiSettings(
        "medium", "zh", tmp_path / "outputs", True, False, True, "820x620"
    )
    request = build_request(
        "job-1", "https://example.com/v?a=1&b=2", settings, tmp_path / "temp"
    )
    assert request.url.endswith("&b=2")
    assert request.language == "zh"
    assert request.keep_audio is True


def test_language_labels_have_stable_protocol_values():
    assert LANGUAGE_LABELS == {"自动检测": None, "中文": "zh", "英文": "en"}


def test_stage_status_marks_previous_stages_complete():
    status = stage_status("transcribe")
    assert status["probe"] == "done"
    assert status["download"] == "done"
    assert status["normalize"] == "done"
    assert status["transcribe"] == "active"
    assert status["export"] == "pending"


def test_stage_status_marks_every_stage_complete_for_done():
    assert stage_status("done") == {
        "probe": "done",
        "download": "done",
        "normalize": "done",
        "transcribe": "done",
        "export": "done",
    }


def test_attempt_start_reports_launch_failure_without_raising():
    def fail() -> None:
        raise OSError("无法创建工作进程")

    started, detail = attempt_start(fail)

    assert started is False
    assert detail == "OSError: 无法创建工作进程"


def test_cleanup_failed_start_removes_only_current_task_root(tmp_path: Path):
    task_root = tmp_path / "task-1"
    sibling_root = tmp_path / "task-2"
    task_root.mkdir()
    sibling_root.mkdir()
    (task_root / "request.json").write_text("{}", encoding="utf-8")
    (sibling_root / "request.json").write_text("{}", encoding="utf-8")

    cleanup_failed_start(task_root)

    assert not task_root.exists()
    assert (sibling_root / "request.json").is_file()


class FakeController:
    is_running = False

    def start(self, request, on_message, on_exit):
        raise AssertionError("invalid URL must not start worker")


def test_advanced_settings_dialog_starts_closed(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)

    assert window.advanced_dialog.isHidden()
    assert not window.idle_hint.isHidden()
    assert not window.open_txt_button.isEnabled()


def test_labels_do_not_receive_global_background_blocks(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)

    assert "QMainWindow, QWidget#centralSurface" in window.styleSheet()
    assert "QMainWindow, QWidget { background:" not in window.styleSheet()


def test_stop_button_has_hover_and_pressed_feedback(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)

    style = window.styleSheet()
    assert "QPushButton#dangerButton:hover:!disabled" in style
    assert "QPushButton#dangerButton:pressed:!disabled" in style
    assert "QPushButton#dangerButton { color: #a53e45; background: #fff0f0; border: 1px solid transparent; }" in style
    assert "border-color: #f2bfc3" in style


def test_checkbox_checked_indicator_uses_high_contrast_surface(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)

    assert isinstance(window.keep_audio_check, ClearCheckBox)
    assert window.keep_audio_check.LABEL_GAP == 5
    assert window.keep_audio_check.sizeHint().width() > window.fontMetrics().horizontalAdvance(
        window.keep_audio_check.text()
    )


def test_invalid_url_is_shown_inline_without_starting_worker(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)
    window.url_entry.setText("not a URL")

    window.start_task()

    assert "网址" in window.status_label.text() or "链接" in window.status_label.text()
    assert window.log.isHidden()


def test_advanced_settings_dialog_and_settings_round_trip(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)

    window.advanced_button.click()
    assert window.advanced_dialog.isVisible()
    window.model_box.setCurrentText("medium")
    window.language_box.setCurrentText("中文")
    window.keep_audio_check.setChecked(True)
    settings = window.current_settings()

    assert settings.model == "medium"
    assert settings.language == "zh"
    assert settings.keep_audio is True


def test_advanced_settings_do_not_reflow_main_window(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)
    window.show()
    original_geometry = window.geometry()
    window.advanced_button.click()
    qtbot.wait(30)

    assert window.model_box.minimumHeight() >= 36
    assert window.language_box.minimumHeight() >= 36
    assert window.output_entry.minimumHeight() >= 36
    fields = (window.model_box, window.language_box, window.output_entry)
    assert all(not field.geometry().isEmpty() for field in fields)
    assert window.open_output_check.isVisible()
    assert window.geometry() == original_geometry
    assert not window.content_card.isHidden()


def test_compact_window_keeps_advanced_options_in_separate_dialog(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)
    window.resize(780, 620)
    window.show()
    window.advanced_button.click()
    qtbot.wait(50)

    assert window.advanced_dialog.isVisible()
    assert window.open_output_check.isVisible()


def test_progress_updates_are_dispatched_on_gui_thread(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)
    window.active_task_id = "job"
    caller_thread = QApplication.instance().thread()
    observed = []
    original = window.handle_message

    def record(message):
        observed.append(QApplication.instance().thread() == caller_thread)
        original(message)

    window.handle_message = record
    window.worker_events.put_message(
        "job", {"type": "progress", "stage": "download", "message": "正在下载音频"}
    )
    window._poll_worker_events()

    assert observed == [True]
    assert window.status_label.text() == "正在下载音频"
    assert "✓" in window.stage_labels["probe"].text()
    assert "●" in window.stage_labels["download"].text()


def test_completed_message_exposes_result_actions(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)
    window.open_output_check.setChecked(False)
    txt = tmp_path / "transcript.txt"
    srt = tmp_path / "transcript.srt"
    window.handle_message(
        {"type": "completed", "paths": [str(txt), str(srt)]}
    )

    assert window.open_dir_button.isEnabled()
    assert window.open_txt_button.isEnabled()
    assert window.open_srt_button.isEnabled()
    assert str(tmp_path) in window.result_summary.text()


def test_copy_detail_uses_application_clipboard(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)
    window.last_detail = "trace details"

    window.copy_detail()

    assert QApplication.clipboard().text() == "trace details"


def test_exit_event_restores_controls(qtbot, tmp_path: Path):
    window = VideoTextWindow(FakeController(), tmp_path, tmp_path)
    qtbot.addWidget(window)
    window._set_running(True)
    window.active_task_id = "job"
    window.handle_exit(WorkerExit(0, False, ""))

    assert window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()

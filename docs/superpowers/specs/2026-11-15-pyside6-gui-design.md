# PySide6 GUI Migration Design

## Goal

Replace the existing Tkinter user interface with a polished PySide6 desktop interface while preserving VideoText's local transcription workflow and worker process behavior.

## Scope

This change is limited to the GUI layer. It retains the existing worker request protocol, worker process, event queue semantics, transcription service, settings fields and persistence location, output formats, and task cancellation behavior. It does not add task history, batch queues, subtitle editing, remote access, or a web interface.

## User experience

The application is a single-window transcription workbench:

- A compact header shows the VideoText name, a short description, and a settings entry point if needed by the final layout.
- The primary input card contains the video URL field, a paste action, and a prominent Start Transcription button. While a job is active, starting another job is disabled and a Stop Task action is available.
- Advanced settings are collapsed by default. When expanded, they expose the model, language, output directory, keep-audio option, overwrite option, and open-output-after-completion option.
- A progress area displays the real processing stages: read media information, download audio, normalize audio, GPU transcription, and write results. It shows the current stage and elapsed time; it does not invent a percentage when the worker cannot report one.
- A content area provides an idle hint, task log updates while running, and a completion summary with output location and actions to open the output directory and generated TXT/SRT files. Failures show a concise actionable message and preserve a copy-diagnostics action.
- The visual system uses a light background, restrained card surfaces, consistent typography and spacing, and one accent color for primary actions and the active stage. The URL and log/content areas expand with the window.

## Architecture and event flow

PySide6 widgets run on the GUI thread. Existing worker callbacks continue to enqueue worker messages and exits through `WorkerEventQueue`. A Qt timer drains that queue on the GUI thread and updates widgets. Background worker threads must never mutate Qt widgets directly. The existing worker protocol remains the source of task lifecycle and progress data.

The current Tkinter GUI module is replaced or adapted as the PySide6 entry point. CLI behavior remains unchanged. The `videotext-gui` command continues to launch the desktop application. PySide6 is added as an application dependency, with version constraints selected during implementation based on project compatibility.

## Settings and compatibility

Keep the current settings path and persisted settings fields, including window geometry, model, language, output directory, and the boolean options. Continue loading existing settings with the current settings loader. Preserve existing output actions and Windows launch behavior. The GUI must continue to support Windows 10/11 and Python 3.11+.

## Error handling and lifecycle

URL validation errors are presented near the input or in a concise dialog without starting a worker. Worker startup failures, worker-reported failures, cancellations, crashes, and unexpected exit codes remain distinguishable. Closing during a running task prompts before cancellation and shutdown, preserving current behavior. Diagnostic detail remains copyable.

## Verification

Update GUI tests for the new framework and retain tests for controller, event queue, protocol, and service behavior. Verify the application can start, load settings, validate URLs, render worker stage updates, stop an active task, handle success and failures, expose output actions, and close cleanly. Run the project's relevant test and lint commands after implementation.

## Design self-review

- No placeholder requirements remain.
- GUI responsibilities are separated from worker execution and domain logic.
- Progress display is constrained to data the worker actually provides.
- Scope excludes unrelated product features and preserves the current CLI.

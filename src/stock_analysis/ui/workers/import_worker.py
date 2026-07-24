"""Run blocking import operations off the UI thread."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

OperationBuilder = Callable[
    [Callable[[int, int], None], threading.Event],
    Callable[[], Any],
]


class ImportBridge(QObject):
    """Lives on the UI thread; receives callbacks from a plain Python worker thread."""

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None

    def start(
        self,
        operation: Callable[[], Any] | None,
        *,
        operation_builder: OperationBuilder | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Import already running")

        if cancel_event is None:
            cancel_event = threading.Event()

        def worker_main() -> None:
            try:
                if operation_builder is not None:

                    def emit_progress(current: int, total: int) -> None:
                        self.progress.emit(current, total)

                    run = operation_builder(emit_progress, cancel_event)
                    result = run()
                elif operation is not None:
                    result = operation()
                else:
                    raise RuntimeError("ImportBridge has no operation configured")
                self.finished.emit(result)
            except Exception as exc:
                self.error.emit(str(exc))

        self._thread = threading.Thread(target=worker_main, daemon=True, name="import-worker")
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def run_in_background(
    parent,
    operation: Callable[[], Any] | None = None,
    *,
    operation_builder: OperationBuilder | None = None,
    title: str = "Working…",
    maximum: int = 0,
    on_success: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> threading.Event:
    from PySide6.QtWidgets import QProgressDialog

    if operation is None and operation_builder is None:
        raise ValueError("run_in_background requires operation or operation_builder")
    if cancel_event is None:
        cancel_event = threading.Event()

    dialog = QProgressDialog(title, "Cancel", 0, max(maximum, 1), parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumDuration(0)
    dialog.setModal(True)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    if maximum <= 0:
        dialog.setRange(0, 0)
    else:
        dialog.setValue(0)

    bridge = ImportBridge()

    def cleanup() -> None:
        dialog.close()

    def handle_success(result: object) -> None:
        cleanup()
        if on_success:
            on_success(result)

    def handle_error(message: str) -> None:
        cleanup()
        if on_error:
            on_error(message)

    def handle_progress(current: int, total: int) -> None:
        if total > 0:
            dialog.setMaximum(total)
            dialog.setValue(current)
            dialog.setLabelText(f"{title}\n{current:,} / {total:,}")
        if on_progress:
            on_progress(current, total)

    def handle_cancel() -> None:
        cancel_event.set()
        dialog.setLabelText(f"{title}\nCancelling…")

    bridge.finished.connect(handle_success, Qt.ConnectionType.QueuedConnection)
    bridge.error.connect(handle_error, Qt.ConnectionType.QueuedConnection)
    bridge.progress.connect(handle_progress, Qt.ConnectionType.QueuedConnection)
    dialog.canceled.connect(handle_cancel)

    # Keep strong references until the dialog closes — prevents GC while the thread runs.
    dialog._import_bridge = bridge  # type: ignore[attr-defined]
    dialog._import_cancel_event = cancel_event  # type: ignore[attr-defined]

    bridge.start(
        operation,
        operation_builder=operation_builder,
        cancel_event=cancel_event,
    )
    dialog.show()
    return cancel_event

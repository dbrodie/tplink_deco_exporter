import asyncio
import signal
from unittest.mock import MagicMock, patch

import pytest

from tplink_deco_exporter.exceptions import UnexpectedApiException
from tplink_deco_exporter.main import (
    _exception_summary,
    _install_signal_handlers,
    _run_tasks_until_shutdown,
)


def test_api_exception_summary_includes_safe_endpoint_context():
    error = UnexpectedApiException(
        "log_export/types:read result type=dict expected=list"
    )

    assert _exception_summary(error) == (
        "UnexpectedApiException: "
        "log_export/types:read result type=dict expected=list"
    )


def test_unknown_exception_summary_does_not_include_potential_secret_text():
    error = RuntimeError("request failed at ;stok=secret-session-token")

    assert _exception_summary(error) == "RuntimeError"


@pytest.mark.asyncio
async def test_signal_handlers_request_shutdown():
    stop_event = asyncio.Event()
    loop = MagicMock()

    with patch("asyncio.get_running_loop", return_value=loop):
        installed_loop, installed_signals = _install_signal_handlers(stop_event)

    assert installed_loop is loop
    assert installed_signals == [signal.SIGTERM, signal.SIGINT]
    callbacks = [call.args[1] for call in loop.add_signal_handler.call_args_list]
    callbacks[0]()
    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_shutdown_cancels_and_awaits_polling_tasks():
    stop_event = asyncio.Event()
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def polling_task():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    task = asyncio.create_task(polling_task())
    await started.wait()
    stop_event.set()

    await _run_tasks_until_shutdown([task], stop_event)

    assert task.cancelled()
    assert cleaned_up.is_set()

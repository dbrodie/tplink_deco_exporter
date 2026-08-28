from tplink_deco_exporter.exceptions import UnexpectedApiException
from tplink_deco_exporter.main import _exception_summary


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

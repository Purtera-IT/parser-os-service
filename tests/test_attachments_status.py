from __future__ import annotations

from types import SimpleNamespace

from parser_os_service.server.routes.compile import _attachments_status


def test_attachments_status_marks_corrupt_tabular_as_failed() -> None:
    result = SimpleNamespace(
        atoms=[],
        warnings=["artifact_parse_error:art_xlsx:corrupt_xlsx:BadZipFile:bad zip"],
        manifest=SimpleNamespace(
            artifact_fingerprints=[
                SimpleNamespace(
                    artifact_id="art_xlsx",
                    filename="broken.xlsx",
                )
            ]
        ),
    )
    manifest = {
        "artifacts": [
            {"attachment_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "filename": "broken.xlsx"},
        ]
    }
    status = _attachments_status(result, manifest)
    row = status["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
    assert row["parse_status"] == "failed"
    assert row["error"].startswith("corrupt_xlsx:")


def test_attachments_status_empty_workbook_is_intake_only() -> None:
    result = SimpleNamespace(
        atoms=[],
        warnings=[],
        manifest=SimpleNamespace(
            artifact_fingerprints=[
                SimpleNamespace(artifact_id="art_empty", filename="empty.xlsx")
            ]
        ),
    )
    manifest = {
        "artifacts": [
            {"attachment_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "filename": "empty.xlsx"},
        ]
    }
    status = _attachments_status(result, manifest)
    assert status["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]["parse_status"] == "intake_only"

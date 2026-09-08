"""The service runs the compile, so the compile has to be able to speak.

Nothing configured logging here, so the root logger sat at WARNING with no
handler and every `logging.getLogger("app.core.…")` line the pipeline emits
went nowhere. From outside, a stage that ran and a stage that was switched off
looked identical — which is how a fusion pass stayed disabled through six
attempts to fix what it was doing.
"""

from __future__ import annotations

import ast
import io
import logging
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "src/parser_os_service/server/app.py"


def _load_configure():
    """Load just the function, without importing the service's dependencies."""
    tree = ast.parse(APP.read_text())
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_configure_logging"
    )
    module = ast.Module(
        body=[
            ast.Import(names=[ast.alias(name="logging")]),
            ast.Import(names=[ast.alias(name="os")]),
            ast.Import(names=[ast.alias(name="sys")]),
            fn,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace: dict = {}
    exec(compile(module, "<test>", "exec"), namespace)
    return namespace["_configure_logging"]


def _fresh_root():
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    return root


def test_a_pipeline_log_line_reaches_stdout() -> None:
    configure = _load_configure()
    root = _fresh_root()
    configure()
    buffer = io.StringIO()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.stream = buffer
    logging.getLogger("app.core.entity_resolution").info("site_fusion: 2 keys")
    assert "site_fusion: 2 keys" in buffer.getvalue()


def test_it_runs_at_info_so_stage_lines_are_visible() -> None:
    configure = _load_configure()
    root = _fresh_root()
    configure()
    assert root.level == logging.INFO


def test_the_level_is_tunable(monkeypatch) -> None:
    configure = _load_configure()
    monkeypatch.setenv("PARSER_OS_LOG_LEVEL", "warning")
    root = _fresh_root()
    configure()
    assert root.level == logging.WARNING


def test_calling_it_twice_does_not_double_log(monkeypatch) -> None:
    """Import order must not produce two copies of every line."""
    monkeypatch.delenv("PARSER_OS_LOG_LEVEL", raising=False)
    configure = _load_configure()
    root = _fresh_root()
    configure()
    before = len(root.handlers)
    configure()
    assert len(root.handlers) == before


def test_it_is_called_at_import_time() -> None:
    """A function nothing calls configures nothing."""
    tree = ast.parse(APP.read_text())
    called = [
        n for n in tree.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", "") == "_configure_logging"
    ]
    assert called, "_configure_logging is defined but never called at import"

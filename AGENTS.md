# AGENTS.md — xjfx

Utility library extending the Python standard library. Single-module package:
all public API lives in `src/xjfx/__init__.py`.

Public API: `DEVNULL`, `PIPE`, `STDOUT` (re-exported from `subprocess`);
`ProcData`; `exec_cmd`; `async_exec_cmd`; `get_answer`; `get_yes`; `grouper`;
`GrouperIncomplete`; `setup_logging`; `thr_exec`.

## CONVENTIONS

- Line length: **128** characters (see `pyproject.toml [tool.ruff] line-length`).
- Style checker: `ruff check` (tox env `check_format`).
- Semantic linter: `pylint` (tox env `check_lint`).
- Formatter: `ruff format` + `isort` + `pyupgrade` (tox env `fix_format`; not
  verified by the `analyze` label — run manually before submitting).
- Import order: `isort` with `profile = "black"` (tox env `check_import`).
- Type checking: `mypy` strict (`strict = true` in `pyproject.toml`; tox env `check_type`).
- Language level: `pyupgrade --py311-plus` (tox env `check_upgrade`; not in `analyze` label).
- Docstrings: single-sentence summary; no sphinx syntax required (xjfx uses
  plain docstrings).

## VERIFICATION

Run the formatter first, then the full analyze suite:

```bash
tox -e fix_format   # pyupgrade + isort + ruff format (not checked by analyze)
tox -m analyze      # build, test, check_type, check_import, check_format, check_lint
```

All environments must pass. Fix any mypy, ruff, isort, or pylint errors before completing.

## NOTES

- `async_exec_cmd` is the async counterpart to `exec_cmd`.  It uses
  `asyncio.create_subprocess_exec` and drains stdout/stderr concurrently via
  `asyncio.gather` to avoid pipe-buffer deadlock.  It does **not** support
  text-mode streams (`text=`, `encoding=`, `errors=`, `universal_newlines=`);
  passing any of these raises `ValueError`.  `ProcData.stdout` and
  `ProcData.stderr` are always `bytes`; decode at the callsite.

- `asyncio.subprocess.PIPE` equals `subprocess.PIPE` (both are `-1`); the
  `PIPE` constant already re-exported from `subprocess` is safe to pass to
  `asyncio.create_subprocess_exec`.

- `_async_iterate_proc_output` drains `asyncio.StreamReader` via `read(4096)`
  chunks, **not** `async for line in stream`.  The `async for` / `readline()`
  path has a default 64 KiB per-line buffer limit and raises `LimitOverrunError`
  on output that contains no newlines (binary data, large JSON blobs, progress
  bars).  Drain with `read(N)` and use `splitlines(keepends=True)` for
  per-line logging.

- `ProcData.stdout` and `ProcData.stderr` are `bytes | str`.  The union is
  genuine: `exec_cmd` returns `bytes` in binary mode (the default) and `str`
  when `text=True` / `encoding=` / `errors=` / `universal_newlines=` is passed
  via `**kwargs`.  The uncaptured-stream sentinel matches the mode:
  `b""` in binary mode, `""` in text mode.

- The `**kwargs` passthrough on both `exec_cmd` and `async_exec_cmd` exists for
  forward compatibility.  `asyncio.create_subprocess_exec` accepts a different
  (generally overlapping) set of kwargs than `Popen`; `shell=True` in particular
  is not accepted and must use `create_subprocess_shell` instead.

- `stdout=None` (inherit parent stdout) is handled implicitly by both
  `Popen` and `create_subprocess_exec` — no special casing needed.

## KNOWN LIMITATIONS

These are pre-existing behaviours, not regressions. Deferred for future work:

- **`ProcData` type union** — `bytes | str` is genuine but forces callers to
  type-narrow before use.  Making `ProcData` generic (`ProcData[bytes]` /
  `ProcData[str]`) would give type-checker accuracy at callsites but would
  ripple `ProcData[bytes]` annotations through the codebase.

- **No cancellation / timeout support** — neither `exec_cmd` nor
  `async_exec_cmd` wraps the subprocess in `asyncio.wait_for` or calls
  `proc.kill()` on cancellation.  Cancelling a task mid-drain orphans the
  subprocess.

- **`_iterate_proc_output` mode detection** — uses `isinstance(stream.read(0), bytes)`
  to distinguish binary vs text streams.  Fragile for exotic stream subclasses;
  adequate for `Popen` usage.

- **`thr_exec` swallows exceptions** — logs errors but never re-raises.
  Callers cannot detect per-task failures.

- **`setup_logging` no-op when already configured** — calls `logging.basicConfig`
  unconditionally; silently does nothing if the root logger already has handlers.

- **Non-UTF-8 subprocess output** — `raw_line.decode()` in both
  `_iterate_proc_output` and `_async_iterate_proc_output` raises
  `UnicodeDecodeError` on non-UTF-8 bytes.

"""
Scenario tests for each public function in xjfx.
"""

import collections.abc
import logging
import pathlib

import pytest
from pytest_mock import MockerFixture

import xjfx

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def restore_logging() -> collections.abc.Generator[None, None, None]:
    """
    Restore the log record factory, root logger level, and root logger handlers
    after every test so that `setup_logging` calls do not leak state across tests.
    Removing handlers ensures `logging.basicConfig` is not a no-op on subsequent
    calls within the same process.
    """
    root = logging.getLogger()
    original_factory = logging.getLogRecordFactory()
    original_level = root.level
    original_handlers = root.handlers[:]
    yield
    logging.setLogRecordFactory(original_factory)
    root.setLevel(original_level)
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in original_handlers:
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# exec_cmd
# ---------------------------------------------------------------------------


def test_exec_cmd_captures_stdout() -> None:
    """stdout is captured and retcode is zero for a simple echo."""
    result = xjfx.exec_cmd(["echo", "hello"])
    assert result.stdout == b"hello\n"
    assert result.stderr == b""
    assert result.retcode == 0


def test_exec_cmd_captures_stderr() -> None:
    """stderr is captured separately when stdout is discarded."""
    result = xjfx.exec_cmd(
        ["python3", "-c", "import sys; print('err', file=sys.stderr)"],
        stdout=xjfx.DEVNULL,
    )
    assert result.stderr == b"err\n"


def test_exec_cmd_combined_streams() -> None:
    """With stderr=STDOUT the error output appears in stdout, not stderr."""
    result = xjfx.exec_cmd(
        ["python3", "-c", "import sys; print('combined', file=sys.stderr)"],
        stderr=xjfx.STDOUT,
    )
    assert b"combined\n" in result.stdout
    # stderr field retains its initial empty value because it is never populated
    assert result.stderr == ""


def test_exec_cmd_with_input() -> None:
    """Bytes provided via `input` are forwarded to the subprocess stdin."""
    result = xjfx.exec_cmd(["cat"], input=b"hello\n")
    assert result.stdout == b"hello\n"


def test_exec_cmd_with_cwd(tmp_path: pathlib.Path) -> None:
    """The `cwd` argument changes the working directory of the subprocess."""
    result = xjfx.exec_cmd(["pwd"], cwd=str(tmp_path))
    assert result.stdout.strip() == str(tmp_path).encode()


def test_exec_cmd_nonzero_retcode_logs_error(mocker: MockerFixture) -> None:
    """A non-zero return code is recorded and triggers an error log."""
    mock_error = mocker.patch.object(xjfx.logger, "error")
    result = xjfx.exec_cmd(["false"])
    assert result.retcode == 1
    mock_error.assert_called()


def test_exec_cmd_ignore_retcode_suppresses_log(mocker: MockerFixture) -> None:
    """Setting ignore_retcode=True prevents _display_proc_error from being called."""
    mock_display = mocker.patch.object(xjfx, "_display_proc_error")
    result = xjfx.exec_cmd(["false"], ignore_retcode=True)
    assert result.retcode == 1
    mock_display.assert_not_called()


def test_exec_cmd_concurrent_stdout_stderr() -> None:
    """
    Simultaneously capturing stdout and stderr does not deadlock even when each
    stream carries more than a full pipe buffer (~64 KiB on Linux).

    The old sequential implementation drained stdout to EOF before touching
    stderr.  If the subprocess filled the stderr pipe buffer before stdout was
    exhausted, both sides would block forever.
    """
    # 128 KiB per stream — well above the typical 64 KiB kernel pipe buffer.
    # Build the payload inside the child process to avoid hitting ARG_MAX.
    nbytes = 128 * 1024
    script = (
        "import sys; "
        f"data = 'x' * {nbytes}; "
        "sys.stdout.write(data); sys.stdout.flush(); "
        "sys.stderr.write(data); sys.stderr.flush()"
    )
    result = xjfx.exec_cmd(["python3", "-c", script])
    assert len(result.stdout) == nbytes
    assert len(result.stderr) == nbytes


# ---------------------------------------------------------------------------
# get_answer
# ---------------------------------------------------------------------------


def test_get_answer_exact_match(mocker: MockerFixture) -> None:
    """An exact match in the accept list returns True."""
    mocker.patch("builtins.input", return_value="yes")
    assert xjfx.get_answer("prompt", ["yes", "y", ""]) is True


def test_get_answer_empty_string_accepted(mocker: MockerFixture) -> None:
    """An empty string is accepted when it appears in the accept list."""
    mocker.patch("builtins.input", return_value="")
    assert xjfx.get_answer("prompt", ["yes", "y", ""]) is True


def test_get_answer_case_insensitive_input(mocker: MockerFixture) -> None:
    """Input is lowercased before comparison, so 'YES' matches 'yes'."""
    mocker.patch("builtins.input", return_value="YES")
    assert xjfx.get_answer("prompt", ["yes"]) is True


def test_get_answer_lower_false_exact_lower_match(mocker: MockerFixture) -> None:
    """With lower=False, already-lowercase input still matches a lowercase accept value."""
    mocker.patch("builtins.input", return_value="yes")
    assert xjfx.get_answer("prompt", ["yes"], lower=False) is True


def test_get_answer_reject_unknown(mocker: MockerFixture) -> None:
    """Input not in the accept list returns False."""
    mocker.patch("builtins.input", return_value="no")
    assert xjfx.get_answer("prompt", ["yes", "y", ""]) is False


def test_get_answer_lower_false_input_always_lowered(mocker: MockerFixture) -> None:
    """
    Even with lower=False, input() is unconditionally lowercased before comparison,
    so 'YES' becomes 'yes' which does not match the accept value 'YES'.
    """
    mocker.patch("builtins.input", return_value="YES")
    assert xjfx.get_answer("prompt", ["YES"], lower=False) is False


# ---------------------------------------------------------------------------
# get_yes
# ---------------------------------------------------------------------------


def test_get_yes_y(mocker: MockerFixture) -> None:
    """'y' is accepted as an affirmative answer."""
    mocker.patch("builtins.input", return_value="y")
    assert xjfx.get_yes("Continue?") is True


def test_get_yes_yes(mocker: MockerFixture) -> None:
    """'yes' is accepted as an affirmative answer."""
    mocker.patch("builtins.input", return_value="yes")
    assert xjfx.get_yes("Continue?") is True


def test_get_yes_empty(mocker: MockerFixture) -> None:
    """An empty string (pressing Enter) is accepted as the default yes."""
    mocker.patch("builtins.input", return_value="")
    assert xjfx.get_yes("Continue?") is True


def test_get_yes_prompt_format(mocker: MockerFixture) -> None:
    """The prompt passed to input() includes the '[Y|n]' hint."""
    mock_input = mocker.patch("builtins.input", return_value="y")
    xjfx.get_yes("Continue?")
    prompt_arg: str = mock_input.call_args[0][0]
    assert "[Y|n]" in prompt_arg


def test_get_yes_negative(mocker: MockerFixture) -> None:
    """'n' is not in the accept list and returns False."""
    mocker.patch("builtins.input", return_value="n")
    assert xjfx.get_yes("Continue?") is False


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("restore_logging")
def test_setup_logging_installs_color_factory() -> None:
    """setup_logging replaces the default log record factory with ColorLogRecord."""
    xjfx.setup_logging()
    factory = logging.getLogRecordFactory()
    assert factory.__name__ == "ColorLogRecord"


@pytest.mark.usefixtures("restore_logging")
def test_setup_logging_record_has_color_attrs() -> None:
    """Records produced by the installed factory carry the three color attributes."""
    xjfx.setup_logging()
    factory = logging.getLogRecordFactory()
    record = factory("test.logger", logging.INFO, "file.py", 1, "hello", None, None)
    for attr in ("colorname", "colorlevel", "colormsg"):
        assert hasattr(record, attr), f"record missing attribute {attr!r}"
        assert isinstance(getattr(record, attr), str)


@pytest.mark.usefixtures("restore_logging")
def test_setup_logging_sets_level(mocker: MockerFixture) -> None:
    """The level argument is forwarded to basicConfig."""
    mock_basicconfig = mocker.patch("logging.basicConfig")
    xjfx.setup_logging(logging.DEBUG)
    mock_basicconfig.assert_called_once_with(
        format="%(colorlevel)s%(colormsg)s",
        level=logging.DEBUG,
    )


@pytest.mark.usefixtures("restore_logging")
def test_setup_logging_unknown_levelname_raises() -> None:
    """
    ColorLogRecord.__init__ performs a dict lookup on levelname; a level integer
    that maps to an unknown name triggers a KeyError.
    """
    xjfx.setup_logging()
    factory = logging.getLogRecordFactory()
    custom_level = 37  # not registered, so getLevelName returns "Level 37"
    with pytest.raises(KeyError):
        factory("test.logger", custom_level, "file.py", 1, "hello", None, None)


# ---------------------------------------------------------------------------
# grouper
# ---------------------------------------------------------------------------


def test_grouper_fill_partial() -> None:
    """FILL pads the last chunk with fillvalue when the input is not evenly divisible."""
    result = list(xjfx.grouper("ABCDEFG", 3, fillvalue="x"))
    assert result == [("A", "B", "C"), ("D", "E", "F"), ("G", "x", "x")]


def test_grouper_fill_exact() -> None:
    """FILL produces no padding when the input length is a multiple of n."""
    result = list(xjfx.grouper("ABCDEF", 3))
    assert result == [("A", "B", "C"), ("D", "E", "F")]


def test_grouper_strict_exact() -> None:
    """STRICT succeeds without error when the input is evenly divisible."""
    result = list(xjfx.grouper("ABCDEF", 3, xjfx.GrouperIncomplete.STRICT))
    assert result == [("A", "B", "C"), ("D", "E", "F")]


def test_grouper_strict_partial_raises() -> None:
    """STRICT raises ValueError when the last chunk would be incomplete."""
    with pytest.raises(ValueError):
        list(xjfx.grouper("ABCDEFG", 3, xjfx.GrouperIncomplete.STRICT))


def test_grouper_ignore_partial() -> None:
    """IGNORE silently drops the final incomplete chunk."""
    result = list(xjfx.grouper("ABCDEFG", 3, xjfx.GrouperIncomplete.IGNORE))
    assert result == [("A", "B", "C"), ("D", "E", "F")]


def test_grouper_remainder_partial() -> None:
    """REMAINDER keeps the final incomplete chunk as a shorter tuple."""
    result = list(xjfx.grouper("ABCDEFG", 3, xjfx.GrouperIncomplete.REMAINDER))
    assert result == [("A", "B", "C"), ("D", "E", "F"), ("G",)]


def test_grouper_remainder_single() -> None:
    """REMAINDER handles a remainder of one element correctly."""
    result = list(xjfx.grouper([1, 2, 3, 4], 3, xjfx.GrouperIncomplete.REMAINDER))
    assert result == [(1, 2, 3), (4,)]


@pytest.mark.parametrize("mode", list(xjfx.GrouperIncomplete))
def test_grouper_empty_input(mode: xjfx.GrouperIncomplete) -> None:
    """All incomplete-handling modes produce an empty result for empty input."""
    assert not list(xjfx.grouper([], 3, mode))


def test_grouper_returns_iterator() -> None:
    """grouper returns a lazy iterator, not a materialised list."""
    result = xjfx.grouper("ABCDEF", 3)
    assert isinstance(result, collections.abc.Iterator)


# ---------------------------------------------------------------------------
# thr_exec
# ---------------------------------------------------------------------------


def test_thr_exec_all_tasks_run() -> None:
    """Every submitted task is executed and its side-effect is observable."""
    results: list[int] = []

    def collect(x: int) -> None:
        results.append(x)

    xjfx.thr_exec(collect, [(1,), (2,), (3,)])
    assert sorted(results) == [1, 2, 3]


def test_thr_exec_empty_args() -> None:
    """Passing an empty argument list completes without error."""
    xjfx.thr_exec(lambda: None, [])


def test_thr_exec_max_workers_respected() -> None:
    """max_workers=1 forces serial execution; all tasks still complete."""
    results: list[int] = []

    def collect(x: int) -> None:
        results.append(x)

    xjfx.thr_exec(collect, [(1,), (2,), (3,)], max_workers=1)
    assert sorted(results) == [1, 2, 3]


def test_thr_exec_exception_logged(mocker: MockerFixture) -> None:
    """An exception raised inside a task is caught and logged as an error."""
    mock_logger = mocker.patch.object(xjfx, "logger")

    def explode(x: int) -> None:
        raise RuntimeError("boom")

    xjfx.thr_exec(explode, [(1,)])
    mock_logger.error.assert_called_once()
    logged_message: str = mock_logger.error.call_args[0][0]
    assert "Error executing task" in logged_message


def test_thr_exec_continues_after_exception() -> None:
    """A failure in one task does not prevent the remaining tasks from running."""
    results: list[int] = []

    def partial_fail(x: int) -> None:
        if x == 2:
            raise RuntimeError("intentional")
        results.append(x)

    xjfx.thr_exec(partial_fail, [(1,), (2,), (3,)])
    assert sorted(results) == [1, 3]

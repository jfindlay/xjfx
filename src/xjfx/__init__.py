"""
Collection of simple utility functions and classes that extend standard library functionality.

For convenience, the `DEVNULL`, `STDOUT`, and `PIPE` constants are imported from `subprocess` so that users of `xjfx` do not
need to `import subprocess`.

"""

import enum
import itertools
import logging
import shlex
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from subprocess import DEVNULL  # noqa: F401, pylint: disable=unused-import
from subprocess import PIPE, STDOUT, Popen
from types import TracebackType
from typing import IO, overload

import colorama

__all__ = [
    "DEVNULL",
    "PIPE",
    "ProcData",
    "STDOUT",
    "exec_cmd",
    "get_answer",
    "get_yes",
    "grouper",
    "setup_logging",
    "thr_exec",
]
logger = logging.getLogger(__name__)


class ProcStreamClassifier(enum.IntEnum):
    """
    Enumerate process "streams" used by the `exec_cmd*` functions.
    - OUTPUT: Combined stdout/stderr
    - STDOUT: Standard output
    - STDERR: Standard error
    """

    OUTPUT = enum.auto()
    STDOUT = enum.auto()
    STDERR = enum.auto()


@dataclass
class ProcData:
    """
    Data returned from a command.
    """

    stdout: bytes | str
    stderr: bytes | str
    retcode: int


def _fmt_proc_cmd(args: list[str]) -> list[str]:
    """
    Format command preamble.
    """
    return [
        "%s[executing]%s `%s`",
        colorama.Fore.WHITE + colorama.Style.BRIGHT,
        colorama.Style.RESET_ALL,
        shlex.join(args),
    ]


def _fmt_proc_output(stream_class: ProcStreamClassifier, line: str) -> list[str]:
    """
    Format stdout/stderr logging output.
    """
    fore_color = ""
    match stream_class:
        case ProcStreamClassifier.OUTPUT:
            fore_color = colorama.Fore.LIGHTBLACK_EX
        case ProcStreamClassifier.STDOUT:
            fore_color = colorama.Fore.BLUE
        case ProcStreamClassifier.STDERR:
            fore_color = colorama.Fore.YELLOW
    return [
        # Sometimes the cmd output does not return to the line beginning, so force carriage return
        "    %s[%s]%s %s%s%s\r",
        fore_color + colorama.Style.BRIGHT,
        stream_class.name,
        colorama.Style.RESET_ALL,
        fore_color,
        line.strip(),
        colorama.Style.RESET_ALL,
    ]


@overload
def _iterate_proc_output(stream: IO[bytes], stream_class: ProcStreamClassifier) -> bytes: ...


@overload
def _iterate_proc_output(stream: IO[str], stream_class: ProcStreamClassifier) -> str: ...


def _iterate_proc_output(stream: IO[bytes] | IO[str], stream_class: ProcStreamClassifier) -> bytes | str:
    """
    Iterate over the process's stdout/stderr.
    """
    accumulated_bytes: bytes = b""
    accumulated_str: str = ""
    is_binary = isinstance(stream.read(0), bytes)
    for raw_line in stream:
        if is_binary:
            assert isinstance(raw_line, bytes)
            logger.debug(*_fmt_proc_output(stream_class, raw_line.decode()))
            accumulated_bytes += raw_line
        else:
            assert isinstance(raw_line, str)
            logger.debug(*_fmt_proc_output(stream_class, raw_line))
            accumulated_str += raw_line
    return accumulated_bytes if is_binary else accumulated_str


def _display_proc_error(args: list[str], proc_data: ProcData) -> None:
    """
    Show command data in the case of error.
    """
    logger.error(f"`{shlex.join(args)}` returned: {proc_data.retcode!r}")
    # If the log level is debug or lower, this info was already logged.
    if logger.getEffectiveLevel() > logging.DEBUG:
        if proc_data.stdout:
            logger.error(proc_data.stdout)
        if proc_data.stderr:
            logger.error(proc_data.stderr)


def exec_cmd(
    args: list[str],
    input: bytes | None = None,
    stdout: int | None = PIPE,
    stderr: int | None = PIPE,
    cwd: str | None = None,
    ignore_retcode: bool = False,
    **kwargs,
) -> ProcData:
    """
    Run a command line and:
    - Provide input
    - Watch the output
    - Integrate logging
    - Format the results

    Except for `ignore_retcode`, the arguments are identical to `subprocess.Popen()` and are passed directly to that
    constructor.
    """
    logger.debug(*_fmt_proc_cmd(args))

    with Popen(
        args,
        stdin=None if input is None else PIPE,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        **kwargs,
    ) as proc_desc:
        if input is not None and proc_desc.stdin is not None:
            proc_desc.stdin.write(input)
            proc_desc.stdin.close()
        with ThreadPoolExecutor(max_workers=2) as io_pool:
            stdout_future: Future[bytes | str] | None = None
            stderr_future: Future[bytes | str] | None = None
            if stdout and proc_desc.stdout is not None:
                stdout_future = io_pool.submit(
                    _iterate_proc_output,
                    proc_desc.stdout,
                    ProcStreamClassifier.OUTPUT if stderr == STDOUT else ProcStreamClassifier.STDOUT,
                )
            if stderr and stderr != STDOUT and proc_desc.stderr is not None:
                stderr_future = io_pool.submit(
                    _iterate_proc_output,
                    proc_desc.stderr,
                    ProcStreamClassifier.STDERR,
                )

    proc_data = ProcData(
        stdout="" if stdout_future is None else stdout_future.result(),
        stderr="" if stderr_future is None else stderr_future.result(),
        retcode=proc_desc.returncode,
    )

    if not ignore_retcode and proc_data.retcode != 0:
        _display_proc_error(args, proc_data)
    return proc_data


def get_answer(prompt: str, accept: list[str], lower: bool = True) -> bool:
    """
    Get an answer from the user.  If `lower` is `True`, convert confirmation option strings and user input to lowercase before
    comparison.

    Example:
    ```
    if not xjfx.get_answer("Continue? [Y|n]", ["yes", "y", ""]):
        exit()
    ```
    """
    answer = input(f"{prompt} ").lower()
    if any(answer.lower() == a.lower() if lower else answer == a for a in accept):
        return True
    return False


def get_yes(prompt: str) -> bool:
    """
    Get a yes/no answer.
    """
    return get_answer(f"{prompt} [Y|n]", accept=["yes", "y", ""])


def setup_logging(level: int = logging.INFO) -> None:
    """
    Default to colorized logging using `colorama` and predefined colorized format specs.
    """

    class ColorLogRecord(logging.LogRecord):
        """
        Add colors to logging output
        """

        lvl_colors: dict[str, str] = {
            "CRITICAL": colorama.Fore.RED + colorama.Style.BRIGHT,
            "ERROR": colorama.Fore.RED + colorama.Style.BRIGHT,
            "WARNING": colorama.Fore.YELLOW + colorama.Style.BRIGHT,
            "INFO": colorama.Fore.GREEN + colorama.Style.BRIGHT,
            "DEBUG": colorama.Fore.CYAN + colorama.Style.BRIGHT,
            "NOTSET": colorama.Fore.WHITE + colorama.Style.BRIGHT,
        }
        msg_colors: dict[str, str] = {
            "CRITICAL": colorama.Fore.RED + colorama.Style.BRIGHT,
            "ERROR": colorama.Fore.RED,
            "WARNING": colorama.Fore.YELLOW,
            "INFO": colorama.Fore.GREEN,
            "DEBUG": colorama.Fore.CYAN,
            "NOTSET": colorama.Fore.WHITE,
        }
        name_color: str = colorama.Fore.GREEN + colorama.Style.BRIGHT
        reset: str = colorama.Style.RESET_ALL

        def __init__(
            self,
            name: str,
            level: int,
            pathname: str,
            lineno: int,
            msg: object,
            args: tuple[object, ...] | Mapping[str, object] | None,
            exc_info: (tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None] | None),
            func: str | None = None,
            sinfo: str | None = None,
        ) -> None:
            super().__init__(name, level, pathname, lineno, msg, args, exc_info, func, sinfo)
            self.colorname = f"{self.name_color}[{self.name:17}]{self.reset}"
            self.colorlevel = f"{self.lvl_colors[self.levelname]}[{self.levelname:8}]{self.reset}"
            self.colormsg = f"{self.msg_colors[self.levelname]} {self.getMessage()}{self.reset}"

    logging.setLogRecordFactory(ColorLogRecord)
    logging.basicConfig(format="%(colorlevel)s%(colormsg)s", level=level)


class GrouperIncomplete(enum.Enum):
    """
    Enumerate options for handling incomplete groupings.

    fill: Add elements to last block if it is partial
    strict: Raise `ValueError` if last block is partial
    ignore: Discard elements from partial last block
    remainder: Keep partial last block
    """

    FILL = enum.auto()
    STRICT = enum.auto()
    IGNORE = enum.auto()
    REMAINDER = enum.auto()


def grouper(
    i: Sequence[object],
    n: int,
    incomplete: GrouperIncomplete = GrouperIncomplete.FILL,
    fillvalue: object = None,
) -> Iterator[tuple[object, ...]]:
    """
    Collect data into non-overlapping chunks or blocks.  (Why is this functionality not part of the official `itertools` API?)

    See [`grouper()` example](https://docs.python.org/3/library/itertools.html#itertools-recipes).
    ```
    FILL:      grouper('ABCDEFG', 3, fillvalue='x')                          --> ABC DEF Gxx
    STRICT:    grouper('ABCDEFG', 3, incomplete=GrouperIncomplete.STRICT)    --> ABC DEF ValueError
    IGNORE:    grouper('ABCDEFG', 3, incomplete=GrouperIncomplete.IGNORE)    --> ABC DEF
    REMAINDER: grouper('ABCDEFG', 3, incomplete=GrouperIncomplete.REMAINDER) --> ABC DEF G
    ```
    """
    args = [iter(i)] * n
    match incomplete:
        case GrouperIncomplete.FILL:
            return itertools.zip_longest(*args, fillvalue=fillvalue)
        case GrouperIncomplete.STRICT:
            return zip(*args, strict=True)
        case GrouperIncomplete.IGNORE:
            return zip(*args)
        case GrouperIncomplete.REMAINDER:
            # Can u read it?  One more, unitary iterator for the remainder.
            remainder = iter([tuple(i[-(len(i) % n) :])]) if len(i) % n != 0 else iter(())
            return itertools.chain(zip(*args), remainder)


def thr_exec(func: Callable[..., object], args: list[tuple[object, ...]], max_workers: int | None = None) -> None:
    """
    Special case reduction for executing a set of parallel tasks in a thread pool.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as pool_exec:
        futures: dict[Future[object], tuple[object, ...]] = {pool_exec.submit(func, *al): al for al in args}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as ex:  # pylint: disable=broad-exception-caught
                logger.error(f"Error executing task: {ex}")

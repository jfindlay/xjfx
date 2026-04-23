"""
Collection of simple utility functions and classes that extend standard library functionality.

For convenience, ``DEVNULL``, ``STDOUT``, and ``PIPE`` are re-exported from :mod:`subprocess`
so callers do not need a separate ``import subprocess``.
"""

import asyncio
import enum
import importlib.metadata
import itertools
import logging
import shlex
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from subprocess import (
    DEVNULL,  # noqa: F401, pylint: disable=unused-import
    PIPE,
    STDOUT,
    Popen,
)
from types import TracebackType
from typing import IO, Any, overload

import colorama

__all__ = [
    "DEVNULL",
    "GrouperIncomplete",
    "PIPE",
    "ProcData",
    "STDOUT",
    "__version__",
    "async_exec_cmd",
    "exec_cmd",
    "get_answer",
    "get_yes",
    "grouper",
    "setup_logging",
    "thr_exec",
]
__version__: str = importlib.metadata.version(__name__)
logger = logging.getLogger(__name__)


class ProcStreamClassifier(enum.IntEnum):
    """Enumerate the subprocess stream categories used by the ``exec_cmd`` family.

    :cvar OUTPUT: Combined stdout and stderr (when stderr is redirected to stdout).
    :cvar STDOUT: Standard output only.
    :cvar STDERR: Standard error only.
    """

    OUTPUT = enum.auto()
    STDOUT = enum.auto()
    STDERR = enum.auto()


@dataclass
class ProcData:
    """Collected output and exit status from a finished subprocess.

    :param stdout: Captured standard output; ``bytes`` in binary mode, ``str`` in text mode.
    :param stderr: Captured standard error; ``bytes`` in binary mode, ``str`` in text mode.
    :param retcode: Process exit code as returned by :attr:`subprocess.Popen.returncode`.
    """

    stdout: bytes | str
    stderr: bytes | str
    retcode: int


def _fmt_proc_cmd(args: list[str]) -> list[str]:
    """Format a colorized log-record arguments list announcing command execution.

    :param args: The command tokens that are about to be executed.
    :returns: A ``logger.debug``-ready positional-args list (format string + values).
    """
    return [
        "%s[executing]%s `%s`",
        colorama.Fore.WHITE + colorama.Style.BRIGHT,
        colorama.Style.RESET_ALL,
        shlex.join(args),
    ]


def _fmt_proc_output(stream_class: ProcStreamClassifier, line: str) -> list[str]:
    """Format a colorized log-record arguments list for a single line of subprocess output.

    The color chosen depends on the stream: gray for combined output, blue for stdout,
    yellow for stderr.

    :param stream_class: Which stream the line originated from.
    :param line: The decoded text line (trailing whitespace is stripped on render).
    :returns: A ``logger.debug``-ready positional-args list (format string + values).
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
    """Drain a synchronous subprocess stream, logging each line and accumulating the full output.

    Mode (binary vs. text) is detected from the stream itself via a zero-length read.

    :param stream: An open :class:`~typing.IO` stream attached to a subprocess pipe.
    :param stream_class: Which stream is being drained; controls log colorization.
    :returns: All accumulated output — ``bytes`` for binary streams, ``str`` for text streams.
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


def _is_text_mode(kwargs: dict[str, Any]) -> bool:
    """Return ``True`` when the given :class:`~subprocess.Popen` kwargs request text-mode streams.

    Mirrors the conditions used by typeshed's ``Popen`` overloads: any of
    ``text``, ``universal_newlines``, ``encoding``, or ``errors`` enables text mode.

    :param kwargs: The keyword-argument dict that will be forwarded to ``Popen``.
    :returns: ``True`` if any text-mode keyword is present and truthy (or non-``None``).
    """
    return bool(
        kwargs.get("text")
        or kwargs.get("universal_newlines")
        or kwargs.get("encoding") is not None
        or kwargs.get("errors") is not None
    )


def _display_proc_error(args: list[str], proc_data: ProcData) -> None:
    """Log the command, exit code, and captured output when a subprocess fails.

    If the effective log level is ``DEBUG`` or lower the output was already logged
    line-by-line during draining, so it is not repeated.

    :param args: The command tokens that were executed.
    :param proc_data: The collected output and exit status of the failed process.
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
    **kwargs: Any,
) -> ProcData:
    """Run a subprocess synchronously, logging its output and returning captured results.

    stdout and stderr are drained concurrently in a two-thread pool to prevent pipe-buffer
    deadlock.  All parameters except ``ignore_retcode`` are forwarded to :class:`subprocess.Popen`.

    :param args: Command and its arguments as a list of strings.
    :param input: Optional bytes to write to the process's stdin before closing it.
    :param stdout: stdout disposition; defaults to ``PIPE`` to capture output.
        Pass ``None`` to inherit the parent's stdout, or ``DEVNULL`` to discard.
    :param stderr: stderr disposition; defaults to ``PIPE`` to capture output.
        Pass ``STDOUT`` to merge stderr into stdout, ``None`` to inherit, or ``DEVNULL`` to discard.
    :param cwd: Working directory for the subprocess; ``None`` inherits the caller's cwd.
    :param ignore_retcode: When ``True``, non-zero exit codes are not logged as errors.
    :param kwargs: Additional keyword arguments forwarded verbatim to :class:`subprocess.Popen`.
    :returns: A :class:`ProcData` containing captured stdout, stderr, and the exit code.
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

    empty: bytes | str = "" if _is_text_mode(kwargs) else b""
    proc_data = ProcData(
        stdout=empty if stdout_future is None else stdout_future.result(),
        stderr=empty if stderr_future is None else stderr_future.result(),
        retcode=proc_desc.returncode,
    )

    if not ignore_retcode and proc_data.retcode != 0:
        _display_proc_error(args, proc_data)
    return proc_data


async def _async_iterate_proc_output(
    stream: asyncio.StreamReader,
    stream_class: ProcStreamClassifier,
) -> bytes:
    """Drain an async subprocess stream, logging each decoded line and accumulating output.

    Reads in fixed-size chunks rather than line-by-line to avoid the
    :class:`asyncio.StreamReader` default 64 KiB per-line buffer limit, which raises
    :exc:`asyncio.LimitOverrunError` on binary data or output without newlines.
    Each chunk is split on newlines for per-line logging before accumulation.

    :param stream: The :class:`asyncio.StreamReader` attached to the subprocess pipe.
    :param stream_class: Which stream is being drained; controls log colorization.
    :returns: All accumulated output as raw bytes.
    """
    accumulated: bytes = b""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        for raw_line in chunk.splitlines(keepends=True):
            logger.debug(*_fmt_proc_output(stream_class, raw_line.decode()))
        accumulated += chunk
    return accumulated


async def async_exec_cmd(
    args: list[str],
    input: bytes | None = None,
    stdout: int | None = PIPE,
    stderr: int | None = PIPE,
    cwd: str | None = None,
    ignore_retcode: bool = False,
    **kwargs: Any,
) -> ProcData:
    """Run a subprocess asynchronously without blocking the event loop.

    Signature and semantics match :func:`exec_cmd` with one restriction:
    :func:`asyncio.create_subprocess_exec` does not support text-mode streams.
    Passing ``text``, ``universal_newlines``, ``encoding``, or ``errors`` raises
    :exc:`ValueError`.  :attr:`ProcData.stdout` and :attr:`ProcData.stderr` are always
    ``bytes``; decode at the callsite if needed.

    Both streams are drained concurrently via :func:`asyncio.gather` to prevent
    pipe-buffer deadlock.

    :param args: Command and its arguments as a list of strings.
    :param input: Optional bytes to write to the process's stdin before closing it.
    :param stdout: stdout disposition; defaults to ``PIPE`` to capture output.
        Pass ``None`` to inherit the parent's stdout, or ``DEVNULL`` to discard.
    :param stderr: stderr disposition; defaults to ``PIPE`` to capture output.
        Pass ``STDOUT`` to merge stderr into stdout, ``None`` to inherit, or ``DEVNULL`` to discard.
    :param cwd: Working directory for the subprocess; ``None`` inherits the caller's cwd.
    :param ignore_retcode: When ``True``, non-zero exit codes are not logged as errors.
    :param kwargs: Additional keyword arguments forwarded to :func:`asyncio.create_subprocess_exec`.
    :returns: A :class:`ProcData` containing captured stdout, stderr, and the exit code.
    :raises ValueError: If any text-mode keyword (``text``, ``encoding``, ``errors``,
        ``universal_newlines``) is present in ``kwargs``.
    """
    if _is_text_mode(kwargs):
        raise ValueError(
            "async_exec_cmd does not support text-mode streams; "
            "asyncio.create_subprocess_exec has no text/encoding/errors/universal_newlines "
            "equivalent.  Decode ProcData.stdout/stderr at the callsite instead."
        )

    logger.debug(*_fmt_proc_cmd(args))

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=PIPE if input is not None else None,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        **kwargs,
    )

    if input is not None and proc.stdin is not None:
        proc.stdin.write(input)
        await proc.stdin.drain()
        proc.stdin.close()

    # Mirror the truthy-check form used by exec_cmd (L173/L179) rather than
    # equality-to-PIPE so that raw-fd values behave consistently with sync.
    stdout_coro = (
        _async_iterate_proc_output(
            proc.stdout,
            ProcStreamClassifier.OUTPUT if stderr == STDOUT else ProcStreamClassifier.STDOUT,
        )
        if stdout and proc.stdout is not None
        else None
    )
    stderr_coro = (
        _async_iterate_proc_output(proc.stderr, ProcStreamClassifier.STDERR)
        if stderr and stderr != STDOUT and proc.stderr is not None
        else None
    )

    # Drain both streams concurrently to avoid pipe-buffer deadlock.
    # Filter out None so asyncio.gather never receives an absent coroutine.
    gathered: list[bytes] = list(await asyncio.gather(*[c for c in (stdout_coro, stderr_coro) if c is not None]))

    stdout_data: bytes = gathered[0] if stdout_coro is not None else b""
    stderr_data: bytes = (
        gathered[1] if stdout_coro is not None and stderr_coro is not None else gathered[0] if stderr_coro is not None else b""
    )

    retcode = await proc.wait()

    proc_data = ProcData(stdout=stdout_data, stderr=stderr_data, retcode=retcode)
    if not ignore_retcode and proc_data.retcode != 0:
        _display_proc_error(args, proc_data)
    return proc_data


def get_answer(prompt: str, accept: list[str], lower: bool = True) -> bool:
    """Prompt the user for input and return whether the response matches an accepted answer.

    Example::

        if not xjfx.get_answer("Continue? [Y|n]", ["yes", "y", ""]):
            sys.exit()

    :param prompt: The question text displayed to the user (a trailing space is appended).
    :param accept: List of accepted answer strings that evaluate to ``True``.
    :param lower: When ``True`` (default), both the user's input and ``accept`` values are
        lowercased before comparison, making the match case-insensitive.
    :returns: ``True`` if the user's input matches any entry in ``accept``, ``False`` otherwise.
    """
    answer = input(f"{prompt} ").lower()
    if any(answer.lower() == a.lower() if lower else answer == a for a in accept):
        return True
    return False


def get_yes(prompt: str) -> bool:
    """Prompt the user with a ``[Y|n]`` suffix and return ``True`` for affirmative responses.

    Accepts ``"yes"``, ``"y"``, or an empty string (pressing Enter) as affirmative.
    Delegates to :func:`get_answer` with a fixed ``accept`` list.

    :param prompt: The question text displayed before the ``[Y|n]`` indicator.
    :returns: ``True`` if the user answers affirmatively, ``False`` otherwise.
    """
    return get_answer(f"{prompt} [Y|n]", accept=["yes", "y", ""])


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with colorized output using :mod:`colorama`.

    Installs a custom :class:`logging.LogRecord` factory that adds ANSI color codes to
    the level name, logger name, and message.  Falls back silently if the root logger
    already has handlers (standard :func:`logging.basicConfig` behavior).

    :param level: Minimum log level to emit; defaults to :data:`logging.INFO`.
    """

    class ColorLogRecord(logging.LogRecord):
        """A :class:`logging.LogRecord` subclass that injects ANSI color codes into each record.

        Three extra attributes are set in :meth:`__init__` and referenced by the format string:

        * ``colorname``  — bracketed logger name with bright-green color.
        * ``colorlevel`` — bracketed level name with level-specific color.
        * ``colormsg``   — formatted message with level-specific foreground color.

        :cvar lvl_colors: Mapping from level name to the ANSI color used for the level badge.
        :cvar msg_colors: Mapping from level name to the ANSI color used for the message body.
        :cvar name_color: ANSI color applied to the logger-name badge.
        :cvar reset: ANSI reset sequence appended after every colored segment.
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
            """Initialise the record and compute the three colorized attribute strings.

            :param name: Logger name.
            :param level: Numeric log level.
            :param pathname: Full path of the source file that issued the log call.
            :param lineno: Line number within ``pathname``.
            :param msg: The log message object.
            :param args: Format arguments for ``msg``, or ``None``.
            :param exc_info: Exception tuple or ``None``; passed through to the base class.
            :param func: Name of the function that issued the log call.
            :param sinfo: Stack-info string or ``None``.
            """
            super().__init__(name, level, pathname, lineno, msg, args, exc_info, func, sinfo)
            self.colorname = f"{self.name_color}[{self.name:17}]{self.reset}"
            self.colorlevel = f"{self.lvl_colors[self.levelname]}[{self.levelname:8}]{self.reset}"
            self.colormsg = f"{self.msg_colors[self.levelname]} {self.getMessage()}{self.reset}"

    logging.setLogRecordFactory(ColorLogRecord)
    logging.basicConfig(format="%(colorlevel)s%(colormsg)s", level=level)


class GrouperIncomplete(enum.Enum):
    """Enumerate strategies for handling a partial final chunk in :func:`grouper`.

    :cvar FILL: Pad the last chunk to length ``n`` using ``fillvalue``.
    :cvar STRICT: Raise :exc:`ValueError` if the iterable length is not a multiple of ``n``.
    :cvar IGNORE: Silently discard elements that do not fill the last chunk.
    :cvar REMAINDER: Keep the partial last chunk as a shorter tuple.
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
    """Partition a sequence into non-overlapping chunks of length ``n``.

    Behavior for a partial final chunk is controlled by ``incomplete``::

        FILL:      grouper('ABCDEFG', 3, fillvalue='x')                       --> ABC DEF Gxx
        STRICT:    grouper('ABCDEFG', 3, incomplete=GrouperIncomplete.STRICT) --> ABC DEF ValueError
        IGNORE:    grouper('ABCDEFG', 3, incomplete=GrouperIncomplete.IGNORE) --> ABC DEF
        REMAINDER: grouper('ABCDEFG', 3, incomplete=GrouperIncomplete.REMAINDER) --> ABC DEF G

    See the itertools grouper recipe:
    https://docs.python.org/3/library/itertools.html#itertools-recipes

    :param i: The input sequence to partition.
    :param n: Chunk size; must be a positive integer.
    :param incomplete: Strategy for the partial last chunk; defaults to
        :attr:`GrouperIncomplete.FILL`.
    :param fillvalue: Padding value used when ``incomplete`` is
        :attr:`GrouperIncomplete.FILL`; defaults to ``None``.
    :returns: An iterator of ``n``-tuples (or shorter for ``REMAINDER``).
    :raises ValueError: When ``incomplete`` is :attr:`GrouperIncomplete.STRICT` and
        the sequence length is not a multiple of ``n``.
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
    """Execute a set of homogeneous tasks in parallel using a thread pool.

    Each element of ``args`` is unpacked as positional arguments to ``func``.  Exceptions
    raised by individual tasks are caught, logged as errors, and swallowed — callers cannot
    detect per-task failures.

    :param func: The callable to invoke for each task.
    :param args: List of argument tuples; one thread-pool submission per element.
    :param max_workers: Maximum number of worker threads; ``None`` lets
        :class:`~concurrent.futures.ThreadPoolExecutor` choose based on the CPU count.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as pool_exec:
        futures: dict[Future[object], tuple[object, ...]] = {pool_exec.submit(func, *al): al for al in args}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as ex:  # pylint: disable=broad-exception-caught
                logger.error(f"Error executing task: {ex}")

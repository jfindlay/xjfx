"""
Collection of simple utility functions and classes that extend standard library functionality.

For convenience, the `DEVNULL`, `STDOUT`, and `PIPE` constants are imported from `subprocess` so that users of `xjfx` do not
need to `import subprocess`.
"""
# TODO:
# - Tests
# - exec_cmd
# 	- Multiproc support (including logging) for watching proc stdout and stderr in real time

import asyncio
import collections
import concurrent.futures
import enum
import io
import itertools
import logging
import shlex
import subprocess
import typing

import colorama

DEVNULL = subprocess.DEVNULL
STDOUT = subprocess.STDOUT
PIPE = subprocess.PIPE
LOGGER = logging.getLogger(__name__)


class ProcStreamClassifier(enum.Enum):
    """
    Enumerate process "streams" used by the `exec_cmd*` functions.
    - OUTPUT: Combined stdout/stderr
    - STDOUT: Standard output
    - STDERR: Standard error
    """

    OUTPUT = 0
    STDOUT = 1
    STDERR = 2


class ProcData:
    """
    Data returned from a command.
    """

    def __init__(self, decode_output: bool = True):
        """
        Setup command return object.
        """
        self.decode_output = decode_output
        self._stdout = b""
        self._stderr = b""
        self.retcode = 0

    @property
    def stdout(self) -> str | bytes:
        """
        Transparently decode if necessary.
        """
        return self._stdout.decode() if self.decode_output else self._stdout

    @stdout.setter
    def stdout(self, lines:list[bytes]) ->None:
        """
        Append to stdout.
        """
        self._stdout = lines

    @property
    def stderr(self) -> str | bytes:
        """
        Transparently decode if necessary.
        """
        return self._stderr.decode() if self.decode_output else self._stderr

    @stderr.setter
    def stderr(self, lines:list[bytes]) ->None:
        """
        Append to stderr.
        """
        self._stderr = lines


def _fmt_proc_cmd(args: list[str]) -> list[str]:
    """
    Format command preamble.
    """
    return (
        "%s[executing]%s `%s`",
        colorama.Fore.WHITE + colorama.Style.BRIGHT,
        colorama.Style.RESET_ALL,
        shlex.join(args),
    )


def _fmt_proc_output(stream_class: ProcStreamClassifier, line: str) -> list[str]:
    """
    Format stdout/stderr logging output.
    """
    if stream_class == ProcStreamClassifier.OUTPUT:
        fore_color = colorama.Fore.LIGHTBLACK_EX
    if stream_class == ProcStreamClassifier.STDOUT:
        fore_color = colorama.Fore.BLUE
    elif stream_class == ProcStreamClassifier.STDERR:
        fore_color = colorama.Fore.YELLOW
    return (
        # Sometimes the cmd output does not return to the line beginning, so force carriage return
        "    %s[%s]%s %s%s%s\r",
        fore_color + colorama.Style.BRIGHT,
        stream_class.name,
        colorama.Style.RESET_ALL,
        fore_color,
        line.strip(),
        colorama.Style.RESET_ALL,
    )


def _iterate_proc_output(stream: io.BufferedReader, stream_class: ProcStreamClassifier) -> list[bytes]:
    """
    Iterate over the process's stdout/stderr.
    """
    accumulated_output = b''
    for raw_line in stream:
        LOGGER.debug(*_fmt_proc_output(stream_class, raw_line.decode()))
        accumulated_output += raw_line
    return accumulated_output


async def _iterate_proc_output_async(stream: io.BufferedReader, stream_class: ProcStreamClassifier) -> list[bytes]:
    """
    Iterate over the process's stdout/stderr.
    """
    accumulated_output: b''
    async for raw_line in stream:
        LOGGER.debug(*_fmt_proc_output(stream_class, raw_line.decode()))
        accumulated_output += raw_line
    return accumulated_output


def _display_proc_result(args:list[str], ignore_retcode:bool, proc_data:ProcData)->None:
    '''
    Show command data in the case of error.
    '''
    if not ignore_retcode and proc_data.retcode != 0:
        LOGGER.error(f"`{shlex.join(args)}` returned: {proc_data.retcode!r}")
        # If the log level is debug or lower, this info was already logged.
        if LOGGER.getEffectiveLevel() > logging.DEBUG:
            if proc_data.stdout:
                LOGGER.error(proc_data.stdout)
            if proc_data.stderr:
                LOGGER.error(proc_data.stderr)


def exec_cmd(
    args: list[str],
    input: bytes | None = None,
    stdout: int | None = PIPE,
    stderr: int | None = PIPE,
    cwd: str | None = None,
    ignore_retcode: bool = False,
    decode_output: bool = True,
    **kwargs,
) -> ProcData:
    """
    Run a command line and:
    - Provide input
    - Watch the output
    - Integrate logging
    - Format the results

    Except for `ignore_retcode`, and `decode_output`, the arguments are identical to `subprocess.Popen()` and are passed
    directly to that constructor.

    The `subprocess` module cannot natively watch both stdout and stderr in real time unless stderr is redirected to stdout.
    Thus, this function can watch stdout or stderr but not both unless they are combined.  Combining stdout and stderr streams
    by setting `stderr=subprocess.STDOUT` means they will be indistinguishably returned in `proc_data.stdout`.
    """
    LOGGER.debug(*_fmt_proc_cmd(args))
    proc_data = ProcData(decode_output=decode_output)

    with subprocess.Popen(
        args,
        stdin=None if input is None else PIPE,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        **kwargs,
    ) as proc_desc:
        if input is not None:
            proc_desc.stdin.write(input)
            proc_desc.stdin.flush()
        if stdout:
            proc_data.stdout = _iterate_proc_output(
                proc_desc.stdout, ProcStreamClassifier.OUTPUT if stderr == STDOUT else ProcStreamClassifier.STDOUT
            )
        if stderr and stderr != STDOUT:
            proc_data.stderr = _iterate_proc_output(proc_desc.stderr, ProcStreamClassifier.STDERR)
    proc_data.retcode = proc_desc.returncode

    _display_proc_result(args, ignore_retcode, proc_data)
    return proc_data


async def exec_cmd_async(
    args: list[str],
    input: bytes | None = None,
    stdout: int | None = PIPE,
    stderr: int | None = PIPE,
    cwd: str | None = None,
    ignore_retcode: bool = False,
    decode_output: bool = True,
    **kwargs,
) -> ProcData:
    """
    Similar to `exec_cmd()` but using `asyncio` subprocess primitives.

    Except for `ignore_retcode`, and `decode_output`, the arguments are identical to `asyncio.create_subprocess_exec()` and are
    passed directly to that function, which pass the remainder to `subprocess.Popen()`.
    """
    LOGGER.debug(*_fmt_proc_cmd(args))
    proc_data = ProcData(decode_output=decode_output)
    proc_desc = asyncio.create_subprocess_exec(
        args[0],
        *args[1:],
        stdin=None if input is None else PIPE,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        **kwargs,
    )
    if input is not None:
        proc_desc.stdin.write(input)
        proc_desc.stdin.flush()
    if stdout:
        proc_data.stdout = _iterate_proc_output_async(
            proc_desc.stdout, ProcStreamClassifier.OUTPUT if stderr == STDOUT else ProcStreamClassifier.STDOUT
        )
    if stderr and stderr != STDOUT:
        proc_data.stderr = _iterate_proc_output_async(proc_desc.stderr, ProcStreamClassifier.STDERR)
    proc_data.retcode = proc_desc.returncode

    _display_proc_result(args, ignore_retcode, proc_data)
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


def get_yes(prompt: str):
    """
    Get a yes/no answer.
    """
    return get_answer(f"{prompt} [Y|n]", accept=["yes", "y", ""])


def setup_logging(level: int = logging.INFO):
    """
    Default to colorized logging using `colorama` and predefined colorized format specs.
    """

    class ColorLogRecord(logging.LogRecord):
        """
        Add colors to logging output
        """

        colors: dict[str, dict[str, str] | str] = {
            "lvls": {
                "CRITICAL": colorama.Fore.RED + colorama.Style.BRIGHT,
                "ERROR": colorama.Fore.RED + colorama.Style.BRIGHT,
                "WARNING": colorama.Fore.YELLOW + colorama.Style.BRIGHT,
                "INFO": colorama.Fore.GREEN + colorama.Style.BRIGHT,
                "DEBUG": colorama.Fore.CYAN + colorama.Style.BRIGHT,
                "NOTSET": colorama.Fore.WHITE + colorama.Style.BRIGHT,
            },
            "msgs": {
                "CRITICAL": colorama.Fore.RED + colorama.Style.BRIGHT,
                "ERROR": colorama.Fore.RED,
                "WARNING": colorama.Fore.YELLOW,
                "INFO": colorama.Fore.GREEN,
                "DEBUG": colorama.Fore.CYAN,
                "NOTSET": colorama.Fore.WHITE,
            },
            "name": colorama.Fore.GREEN + colorama.Style.BRIGHT,
            "proc": colorama.Fore.BLUE + colorama.Style.BRIGHT,
            "reset": colorama.Style.RESET_ALL,
        }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.colorname = f"{self.colors['name']}[{self.name:17}]{self.colors['reset']}"
            self.colorlevel = f"{self.colors['lvls'][self.levelname]}[{self.levelname:8}]{self.colors['reset']}"
            self.colormsg = f"{self.colors['msgs'][self.levelname]} {self.getMessage()}{self.colors['reset']}"

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

    FILL = 1
    STRICT = 2
    IGNORE = 3
    REMAINDER = 4


def grouper(
    i: tuple | list,
    n: int,
    incomplete: GrouperIncomplete = GrouperIncomplete.FILL,
    fillvalue: typing.Any = None,
):
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
    if incomplete == GrouperIncomplete.FILL:
        return itertools.zip_longest(*args, fillvalue=fillvalue)
    if incomplete == GrouperIncomplete.STRICT:
        return zip(*args, strict=True)
    if incomplete == GrouperIncomplete.IGNORE:
        return zip(*args)
    if incomplete == GrouperIncomplete.REMAINDER:
        # Can u read it?  One more, unitary iterator for the remainder.
        remainder = iter([tuple(i[-(len(i) % n) :])]) if len(i) % n != 0 else iter(())
        return itertools.chain(zip(*args), remainder)
    raise ValueError("Expected fill, strict, ignore, or remainder")


def thr_exec(func: collections.abc.Callable, args: list[tuple], max_workers: int | None = None):
    """
    Special case reduction for executing a set of parallel tasks in a thread pool.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool_exec:
        for future in concurrent.futures.as_completed({pool_exec.submit(*(func,) + al): al for al in args}):
            try:
                future.result()
            except Exception as ex:
                LOGGER.error(f"Error executing task: {ex}")

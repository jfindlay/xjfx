"""
Collection of simple utility functions and classes that extend standard library functionality.
"""
# TODO:
# - Tests
# - exec_cmd
# 	- Multiproc support (including logging) for watching proc stdout and stderr in real time

import collections
import concurrent.futures
import enum
import itertools
import logging
import shlex
import subprocess
import typing

import colorama

LOGGER = logging.getLogger(__name__)


class ProcStream(enum.Enum):
    """
    Enumerate process "streams" used
    - OUTPUT: Combined stdout/stderr
    - STDOUT: Standard output
    - STDERR: Standard error
    """

    OUTPUT = 0
    STDOUT = 1
    STDERR = 2


def _fmt_proc_output(stream: ProcStream, line: str) -> list[str]:
    """
    Format cmd stdout and stderr logging output
    """
    if stream == ProcStream.OUTPUT:
        fore_color: str = colorama.Fore.LIGHTBLACK_EX
    if stream == ProcStream.STDOUT:
        fore_color = colorama.Fore.BLUE
    elif stream == ProcStream.STDERR:
        fore_color = colorama.Fore.YELLOW
    return [
        # Sometimes the cmd output does not return to the line beginning, so force carriage return
        "    %s[%s]%s %s%s%s\r",
        fore_color + colorama.Style.BRIGHT,
        stream.name,
        colorama.Style.RESET_ALL,
        fore_color,
        line.strip(),
        colorama.Style.RESET_ALL,
    ]


def exec_cmd(
    args: list[str],
    input: bytes | None = None,
    stdout: int | None = subprocess.PIPE,
    stderr: int | None = subprocess.PIPE,
    cwd: str | None = None,
    ignore_retcode: bool = False,
    decode_output: bool = True,
    **kwargs,
) -> dict[str, str | bytes | int]:
    """
    Run a command line and:
    - Provide input
    - Watch the output
    - Integrate logging
    - Format the results
    """

    LOGGER.debug(
        "%s[executing]%s `%s`",
        colorama.Fore.WHITE + colorama.Style.BRIGHT,
        colorama.Style.RESET_ALL,
        shlex.join(args),
    )
    res: dict[str, str | bytes | int] = {
        "stdout": "" if decode_output else b"",
        "stderr": "" if decode_output else b"",
        "retcode": 0,
    }
    with subprocess.Popen(
        args,
        stdin=None if input is None else subprocess.PIPE,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        **kwargs,
    ) as cmd_desc:
        if input and cmd_desc.stdin:
            cmd_desc.stdin.write(input)
            cmd_desc.stdin.flush()
        if stdout and isinstance(cmd_desc.stdout, collections.abc.Iterable):
            for raw_line in cmd_desc.stdout:
                line: str = raw_line.decode()
                LOGGER.debug(
                    *_fmt_proc_output(
                        ProcStream.OUTPUT if stderr == subprocess.STDOUT else ProcStream.STDOUT,
                        line,
                    )
                )
                res["stdout"] += line if decode_output else raw_line
        if stderr and isinstance(cmd_desc.stderr, collections.abc.Iterable) and stderr != subprocess.STDOUT:
            for raw_line in cmd_desc.stderr:
                line = raw_line.decode()
                LOGGER.debug(*_fmt_proc_output(ProcStream.STDERR, line))
                res["stderr"] += line if decode_output else raw_line
    res["retcode"] = cmd_desc.returncode
    if not ignore_retcode and res["retcode"] != 0:
        LOGGER.error(f"`{shlex.join(args)}` returned: {res['retcode']!r}")
        if res["stdout"]:
            LOGGER.error(res["stdout"])
        if res["stderr"]:
            LOGGER.error(res["stderr"])
    return res


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

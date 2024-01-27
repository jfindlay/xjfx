import collections
import enum
import typing
from _typeshed import Incomplete as Incomplete

LOGGER: Incomplete

class ProcStream(enum.Enum):
    OUTPUT: int
    STDOUT: int
    STDERR: int

def exec_cmd(args: list[str], input: bytes | None = None, stdout: int | None = ..., stderr: int | None = ..., cwd: str | None = None, ignore_retcode: bool = False, decode_output: bool = True, **kwargs) -> dict[str, str | bytes | int]: ...
def get_answer(prompt: str, accept: list[str], lower: bool = True) -> bool: ...
def get_yes(prompt: str): ...
def setup_logging(level: int = ...): ...

class GrouperIncomplete(enum.Enum):
    FILL: int
    STRICT: int
    IGNORE: int
    REMAINDER: int

def grouper(i: tuple | list, n: int, incomplete: GrouperIncomplete = ..., fillvalue: typing.Any = None): ...
def thr_exec(func: collections.abc.Callable, args: list[tuple], max_workers: int | None = None): ...

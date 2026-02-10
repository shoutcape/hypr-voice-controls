from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    pattern: str
    argv: list[str]
    label: str
    contains_any: tuple[str, ...] = ()
    contains_all: tuple[str, ...] = ()

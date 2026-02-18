from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    pattern: str
    argv: list[str]
    label: str

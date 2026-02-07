import re

from .models import CommandSpec


COMMANDS = [
    CommandSpec(
        r"^((workspace )?(one|1)|(tyotila|työtila) (yksi|1|ykkonen|ykkönen))$",
        ["hyprctl", "dispatch", "workspace", "1"],
        "Workspace 1",
    ),
    CommandSpec(
        r"^((workspace )?(two|2)|(tyotila|työtila) (kaksi|2|kakkonen))$",
        ["hyprctl", "dispatch", "workspace", "2"],
        "Workspace 2",
    ),
    CommandSpec(
        r"^(volume up|((laita )?(aani|ääni)(ta)? )?kovemmalle)$",
        ["pamixer", "-i", "5"],
        "Volume up",
    ),
    CommandSpec(
        r"^(volume down|((laita )?(aani|ääni)(ta)? )?hiljemmalle)$",
        ["pamixer", "-d", "5"],
        "Volume down",
    ),
    CommandSpec(
        r"^(lock( screen)?|lukitse( naytto| näyttö)?)$",
        ["loginctl", "lock-session"],
        "Lock screen",
    ),
]


def normalize(text: str) -> str:
    clean = re.sub(r"[^a-z0-9äöå ]+", "", text.lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"^(ja|and|please|pliis|hei)\s+", "", clean)
    return clean


def fuzzy_allowlist_match(clean_text: str) -> tuple[list[str] | None, str | None]:
    words = set(clean_text.split())
    compact = clean_text.replace(" ", "")

    workspace_words = {"workspace", "työtila", "tyotila"}
    one_words = {"1", "one", "yksi", "ykkonen", "ykkönen"}
    two_words = {"2", "two", "kaksi", "kakkonen"}

    volume_words = {"volume", "ääni", "aani", "ääntä", "aanta"}
    up_words = {"up", "kovemmalle", "kovemmalla", "louder"}
    down_words = {
        "down",
        "hiljemmalle",
        "hiljemmalla",
        "hiljimmalle",
        "hiljimmälle",
        "hiljemmälle",
        "lower",
    }

    lock_words = {"lock", "lukitse", "lukit", "lukitseen"}
    screen_words = {"screen", "näyttö", "naytto", "näytön", "nayton", "näyttöön", "nayttoon"}

    if (workspace_words & words) and (one_words & words):
        return ["hyprctl", "dispatch", "workspace", "1"], "Workspace 1"

    if (workspace_words & words) and (two_words & words):
        return ["hyprctl", "dispatch", "workspace", "2"], "Workspace 2"

    if any(stem in clean_text for stem in {"hiljem", "hiljim", "hilimm"}) or any(
        stem in compact for stem in {"hiljem", "hiljim", "hilimm"}
    ):
        return ["pamixer", "-d", "5"], "Volume down"

    if any(stem in clean_text for stem in {"kovem", "kuvem"}) or any(
        stem in compact for stem in {"kovem", "kuvem"}
    ):
        return ["pamixer", "-i", "5"], "Volume up"

    if "lisää" in words and ("ääntä" in words or "ääni" in words or "aani" in words):
        return ["pamixer", "-i", "5"], "Volume up"

    if (volume_words & words and up_words & words) or clean_text in {"william up", "volyum up"}:
        return ["pamixer", "-i", "5"], "Volume up"

    if (volume_words & words and down_words & words) or clean_text in {
        "william down",
        "volyum down",
        "hyviemmalle",
    }:
        return ["pamixer", "-d", "5"], "Volume down"

    if (lock_words & words) and ((screen_words & words) or "lock" in words):
        return ["loginctl", "lock-session"], "Lock screen"

    return None, None


def match_command(clean_text: str) -> tuple[list[str] | None, str | None]:
    for command in COMMANDS:
        if re.fullmatch(command.pattern, clean_text):
            return command.argv, command.label
    return fuzzy_allowlist_match(clean_text)

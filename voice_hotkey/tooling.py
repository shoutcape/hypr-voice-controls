import shutil
from functools import lru_cache


@lru_cache(maxsize=None)
def has_tool(tool: str) -> bool:
    return shutil.which(tool) is not None

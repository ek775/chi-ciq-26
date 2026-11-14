import hashlib
from collections.abc import Sequence
from pathlib import Path


def validate_shard(index: int, count: int) -> None:
    if count < 1 or not 0 <= index < count:
        raise ValueError("Shard count must be positive and index in [0, count)")


def shard_paths(
    paths: Sequence[Path], root: Path, *, index: int = 0, count: int = 1
) -> list[Path]:
    validate_shard(index, count)
    return [
        path
        for path in paths
        if int.from_bytes(
            hashlib.sha256(path.relative_to(root).as_posix().encode()).digest(),
            "big",
        )
        % count
        == index
    ]

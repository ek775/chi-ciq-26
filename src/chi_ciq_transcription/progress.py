import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from time import perf_counter

logger = logging.getLogger(__name__)


@contextmanager
def timed_operation(description: str) -> Iterator[None]:
    """Log wall-clock progress without hiding errors or changing their types."""
    started = perf_counter()
    completed = False
    logger.info("%s: started", description)
    try:
        yield
        completed = True
    finally:
        elapsed = perf_counter() - started
        logger.log(
            logging.INFO if completed else logging.ERROR,
            "%s: %s in %s (%.2fs)",
            description,
            "completed" if completed else "failed or interrupted",
            timedelta(seconds=round(elapsed)),
            elapsed,
        )

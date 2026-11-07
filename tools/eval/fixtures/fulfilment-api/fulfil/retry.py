"""Run a call again when it raises one of the listed errors."""

from fulfil import clock


def call(action, attempts, retry_on, backoff=(), on_retry=None):
    """Return the result of `action()`. Re-raise the last error once `attempts` are used up."""
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except retry_on as error:
            if attempt == attempts:
                raise
            if on_retry:
                on_retry(attempt, error)
            if backoff:
                clock.advance(backoff[min(attempt - 1, len(backoff) - 1)])

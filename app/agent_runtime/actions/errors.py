from __future__ import annotations


class RetryableActionError(Exception):
    def __init__(self, code: str = "retryable_error") -> None:
        super().__init__(code)
        self.code = code


class PermanentActionError(Exception):
    def __init__(self, code: str = "permanent_error") -> None:
        super().__init__(code)
        self.code = code

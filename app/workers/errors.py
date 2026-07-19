from __future__ import annotations


class RetryableJobError(Exception):
    def __init__(self, code: str = "retryable_error") -> None:
        super().__init__(code)
        self.code = code


class PermanentJobError(Exception):
    def __init__(self, code: str = "permanent_error") -> None:
        super().__init__(code)
        self.code = code

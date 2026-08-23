from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


class ConfigurationError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("configuration_error", message, False)


class ExternalServiceError(AppError):
    pass

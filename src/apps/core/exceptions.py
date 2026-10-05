"""Domain exceptions raised by the service layer; translated to HTTP by the API/UI layers."""


class DomainError(Exception):
    code = "domain_error"
    status_code = 400

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class ValidationFailed(DomainError):
    code = "validation_failed"
    status_code = 400


class PermissionDenied(DomainError):
    code = "permission_denied"
    status_code = 403


class NotFound(DomainError):
    code = "not_found"
    status_code = 404


class InvalidTransition(DomainError):
    code = "invalid_transition"
    status_code = 409


class Conflict(DomainError):
    code = "conflict"
    status_code = 409


class RateLimited(DomainError):
    code = "rate_limited"
    status_code = 429

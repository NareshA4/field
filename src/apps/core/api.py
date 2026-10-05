"""API foundation: pagination + uniform error envelope.

Error envelope (every non-2xx JSON response):
    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}
"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from . import logging as flogging
from .exceptions import DomainError


class StandardPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200


def _envelope(code, message, details=None):
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": flogging.request_id_var.get(),
        }
    }


def exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response(_envelope(exc.code, exc.message, exc.details), status=exc.status_code)
    if isinstance(exc, DjangoPermissionDenied):
        exc = drf.PermissionDenied()
    if isinstance(exc, Http404):
        exc = drf.NotFound()

    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # unhandled -> Django 500 handling + logging

    if isinstance(exc, drf.ValidationError):
        code, message, details = "validation_failed", "Validation failed.", {"fields": response.data}
    elif isinstance(exc, drf.NotAuthenticated | drf.AuthenticationFailed):
        code, message, details = "not_authenticated", str(exc.detail), {}
    elif isinstance(exc, drf.PermissionDenied):
        code = getattr(exc.detail, "code", None) or getattr(exc, "default_code", "permission_denied")
        message, details = str(exc.detail), {}
    elif isinstance(exc, drf.NotFound):
        code, message, details = "not_found", "Not found.", {}
    elif isinstance(exc, drf.Throttled):
        code, message, details = "throttled", "Too many requests.", {"retry_after": exc.wait}
    else:
        code = getattr(exc, "default_code", "error")
        message, details = str(getattr(exc, "detail", exc)), {}
    response.data = _envelope(code, message, details)
    return response

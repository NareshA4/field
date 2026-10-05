"""Small helpers shared by module APIs (pagination, schema parameters, site-aware permission checks)."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter

from apps.rbac import services as rbac

from .api import StandardPagination
from .exceptions import PermissionDenied

ID_PARAM = OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH)


def query_param(name: str, description: str = "", type_=OpenApiTypes.STR) -> OpenApiParameter:
    return OpenApiParameter(name, type_, OpenApiParameter.QUERY, description=description, required=False)


def paginate(request, qs, serializer_cls, **context):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(qs, request)
    return paginator.get_paginated_response(serializer_cls(page, many=True, context=context).data)


def require_permission(membership, code: str, site=None):
    """Object-level authorization after the object was found in the caller's visible set: 403 when the caller
    can see the object but lacks ``code`` for its site."""
    if not rbac.has_permission(membership, code, site):
        raise PermissionDenied("You do not have permission to perform this action.")

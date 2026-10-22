from typing import Any
from drf_spectacular.views import SpectacularAPIView
from rest_framework.response import Response


class CachedSpectacularAPIView(SpectacularAPIView):
    """Caches the generated OpenAPI schema in memory to prevent expensive dynamic AST / reflection
    introspection on every request. On low-resource environments (e.g. Render Free), dynamic generation
    takes 12-16s per request and blocks the GIL; this view serves subsequent requests in < 1ms."""

    _cached_schema: dict[str, Any] = {}

    def _get_schema_response(self, request):
        version = self.api_version or request.version or self._get_version_parameter(request)
        cache_key = f"{self.urlconf}:{version}:{self.serve_public}"
        if cache_key in self.__class__._cached_schema:
            data = self.__class__._cached_schema[cache_key]
        else:
            generator = self.generator_class(urlconf=self.urlconf, api_version=version, patterns=self.patterns)
            data = generator.get_schema(request=request, public=self.serve_public)
            self.__class__._cached_schema[cache_key] = data

        return Response(
            data=data,
            headers={"Content-Disposition": f'inline; filename="{self._get_filename(request, version)}"'},
        )

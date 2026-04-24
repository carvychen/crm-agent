"""Workaround for a route-template bug in the azure-functions Python SDK.

The SDK hardcodes ``route="/{*route}"`` (leading slash) in
``azure/functions/decorators/function_app.py`` — ``AsgiFunctionApp._add_http_app``
(line 4068 in 1.24.0; line 4318 in 2.1.0). The Functions host concatenates with
its ``routePrefix`` using ``/`` as separator, producing ``<prefix>//{*route}``.
On Flex Consumption's ASP.NET Core 8 host, ``RoutePatternParser`` rejects
consecutive ``/`` and JobHost startup fails with
``RoutePatternException: The route template separator character '/' cannot appear consecutively``.

``FlexAsgiFunctionApp`` drops the leading slash. Combined with ``host.json``'s
``routePrefix=""`` the final template is just ``{*route}`` — a catch-all with no
prefix, matching what ``src/asgi.py`` declares (``/mcp``, ``/api/chat``).

If a future azure-functions release removes the leading slash upstream, this
module can be deleted and ``function_app.py`` can use ``func.AsgiFunctionApp``
directly. ``tests/test_flex_asgi.py`` will fail loudly if that regression
happens silently.
"""
from __future__ import annotations

import azure.functions as func


class FlexAsgiFunctionApp(func.AsgiFunctionApp):
    def _add_http_app(self, http_middleware, function_name="http_app_func"):
        if not isinstance(http_middleware, func.AsgiMiddleware):
            raise TypeError("Please pass AsgiMiddleware instance as parameter.")
        middleware = http_middleware

        @self.function_name(name=function_name)
        @self.http_type(http_type="asgi")
        @self.route(
            methods=(m for m in func.HttpMethod),
            auth_level=self.auth_level,
            route="{*route}",
        )
        async def http_app_func(req, context):
            if not self.startup_task_done:
                success = await middleware.notify_startup()
                if not success:
                    raise RuntimeError("ASGI middleware startup failed.")
                self.startup_task_done = True
            return await middleware.handle_async(req, context)

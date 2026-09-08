"""Mark in-app navigation requests for lightweight HTML responses.

When X-OTB-SPA: 1, templates skip the persistent chrome (sidebar, fonts, tab bar,
app.js). The client swaps only `.main` into the existing shell.
"""


class SpaModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.spa_mode = request.headers.get('X-OTB-SPA') == '1'
        return self.get_response(request)

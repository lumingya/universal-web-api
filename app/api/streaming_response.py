"""Close request generators promptly, including disconnects during ASGI send()."""
import anyio
from starlette.responses import StreamingResponse as StarletteStreamingResponse

from app.services.request_lifecycle import request_finalization


class RequestStreamingResponse(StarletteStreamingResponse):
    def __init__(self, *args, request_context=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_context = request_context
        self._body_closed = False

    async def _close_body(self):
        if self._body_closed:
            return
        try:
            close = getattr(self.body_iterator, 'aclose', None)
            if close is not None:
                with anyio.CancelScope(shield=True):
                    await close()
        finally:
            self._body_closed = True
            # An unstarted async generator has no active finally block. Cover
            # disconnects during headers/before its first iteration as well.
            ctx = self.request_context
            if ctx is not None:
                if not ctx.is_terminal():
                    ctx.request_cancel('stream_closed')
                with request_finalization(ctx):
                    pass

    async def stream_response(self, send):
        try:
            await super().stream_response(send)
        finally:
            # A disconnect while sending a yielded chunk leaves the generator
            # suspended. Cancelling the response task alone does not close it.
            # Close in the iterator's original context; never wait for GC.
            await self._close_body()

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also cover cancellation before the stream task was scheduled.
            await self._close_body()

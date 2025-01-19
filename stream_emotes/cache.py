import asyncio
import time

from sanic.log import logger

def cache(timeout: int):
    def cache_impl(fun):
        cached = {}
        lock = asyncio.Lock()

        async def wrapper(request, **kwargs):
            if len(request.args) > 0:
                # do not cache
                logger.info('Query args present; not caching')
                return await fun(request, **kwargs)

            cache_key = tuple(sorted(kwargs.items()))
            if not cached.get(cache_key) or time.monotonic() > cached[cache_key]['expires']:
                async with lock:
                    logger.info('Cache miss')
                    cached[cache_key] = {
                        'val': await fun(request, **kwargs),
                        'expires': time.monotonic() + timeout,
                    }
            return cached[cache_key]['val']
        return wrapper
    return cache_impl

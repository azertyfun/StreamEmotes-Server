import asyncio
import time

from sanic.log import logger

class CacheItem:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.expires = 0
        self.value = None

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

            async with lock:
                cached.setdefault(cache_key, CacheItem())

            item = cached[cache_key]

            async with item.lock:
                if time.monotonic() > item.expires:
                    async with lock:
                        logger.info('Cache miss')
                        item.value = await fun(request, **kwargs)
                        item.expires = time.monotonic() + timeout
                return item.value
        return wrapper
    return cache_impl

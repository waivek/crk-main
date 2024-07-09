from waivek import Timer   # Single Use
timer = Timer()
from waivek import Code    # Multi-Use
from waivek import handler # Single Use
from waivek import ic, ib     # Multi-Use, import time: 70ms - 110ms
from waivek import rel2abs
Code; ic; ib; rel2abs
from batch_get_all_cookies import batch_get_all_cookies
import requests
from bs4 import BeautifulSoup
from waivek import read, write


import time
import os

import aiohttp
import asyncio

from typing import Callable
from typing import List, Tuple

def should_update_cache(cache_path: str, expiry_seconds: int):
    if not os.path.exists(cache_path) or os.path.getsize(cache_path) == 0:
        return True
    payload = read(cache_path)
    return time.time() - payload["timestamp"] > expiry_seconds

def update_cache(fn: Callable, cache_path: str):
    data = fn()
    payload = {"timestamp": time.time(), "data": data}
    write(payload, cache_path)
    return data

def cached_function_call(fn: Callable, cache_filename: str, expiry_seconds: int=60 * 60 * 24):
    # format: { timestamp: 1234567890, data: "..." }
    cache_dir = rel2abs("cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, cache_filename)
    if should_update_cache(cache_path, expiry_seconds):
        update_cache(fn, cache_path)
    return read(cache_path)["data"]

async def fetch_status(session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore) -> Tuple[str, str]:
    """
    Fetch the status code of a URL with a semaphore to limit concurrency.

    Parameters:
    session (aiohttp.ClientSession): The session object to perform the request.
    url (str): The URL to check.
    semaphore (asyncio.Semaphore): The semaphore to limit concurrency.

    Returns:
    Tuple[str, int]: The URL and its status code.
    """
    async with semaphore:
        try:
            async with session.get(url) as response:
                return url, str(response.status)
        except Exception as e:
            return url, str(e)

async def check_urls(urls: List[str], max_concurrent_requests: int = 10) -> List[Tuple[str, str]]:
    """
    Check if a list of URLs are accessible by fetching their status codes.

    Parameters:
    urls (List[str]): The list of URLs to check.
    max_concurrent_requests (int): The maximum number of concurrent requests.

    Returns:
    List[Tuple[str, int]]: A list of tuples with URLs and their status codes or error messages.
    """
    semaphore = asyncio.Semaphore(max_concurrent_requests)
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_status(session, url, semaphore) for url in urls]
        return await asyncio.gather(*tasks)


# def main():
#     urls = [
#         "https://www.example.com",
#         "https://www.google.com",
#         "https://nonexistent.url"
#     ]
#     results = asyncio.run(check_urls(urls))
#     for url, status in results:
#         print(f"URL: {url}, Status: {status}")


def main():
    table = cached_function_call(batch_get_all_cookies, "batch_get_all_cookies.json")
    prefix = "https://cookierunkingdom.fandom.com"
    urls = [ prefix + row["url"] for row in table ]
    results = asyncio.run(check_urls(urls))
    for url, status in results:
        print(f"URL: {url}, Status: {status}")

if __name__ == "__main__":
    with handler():
        main()


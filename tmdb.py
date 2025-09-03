from cache import Cache
from datetime import timedelta
import httpx
import os
import asyncio

#from dotenv import load_dotenv
#load_dotenv()

# Keep legacy sizes (optional, as images are not forced in detail paths)
TMDB_POSTER_URL = 'https://image.tmdb.org/t/p/w500'
TMDB_BACK_URL = 'https://image.tmdb.org/t/p/original'
TMDB_API_KEY = os.getenv('TMDB_API_KEY')

# Cache set
tmp_cache = Cache(maxsize=100000, ttl=timedelta(days=7).total_seconds())
tmp_cache.clear()


# Too many requests retry
async def fetch_and_retry(client: httpx.AsyncClient, id: str, url: str, params: dict, max_retries=5) -> dict:
    headers = {
        "accept": "application/json"
    }

    for attempt in range(1, max_retries + 1):
        response = await client.get(url, headers=headers, params=params)

        if response.status_code == 200:
            meta_dict = response.json()
            meta_dict['imdb_id'] = id
            tmp_cache.set(id, meta_dict)
            return meta_dict

        elif response.status_code == 429:
            print(response)
            await asyncio.sleep(attempt * 2)

    return {}


# Get from external source id
async def get_tmdb_data(client: httpx.AsyncClient, id: str, source: str) -> dict:
    params = {
        "external_source": source,
        "language": "it-IT",
        "api_key": TMDB_API_KEY
    }

    url = f"https://api.themoviedb.org/3/find/{id}"
    item = tmp_cache.get(id)

    if item != None:
        return item
    else:
        return await fetch_and_retry(client, id, url, params)


# Converting imdb id to tmdb id
async def convert_imdb_to_tmdb(imdb_id: str, preferred_type: str | None = None, bypass_cache: bool = False) -> str:
    """
    Convert an IMDb id (tt...) to a TMDB id string "tmdb:<id>".
    If preferred_type is provided ('movie'|'series'), prefer that type when resolving.
    """
    if not bypass_cache:
        tmdb_data = tmp_cache.get(imdb_id)
    else:
        tmdb_data = None

    if tmdb_data is None:
        async with httpx.AsyncClient(timeout=20) as client:
            tmdb_data = await get_tmdb_data(client, imdb_id, 'imdb_id')
    # store minimal mapping in cache
    tmp_cache.set(imdb_id, tmdb_data)
    return get_id(tmdb_data, preferred_type=preferred_type)
        

# Search and parse id
def get_id(tmdb_data: dict, preferred_type: str | None = None) -> str:
    """Pick a TMDB id from a /find response, optionally preferring movie or tv results."""
    try:
        if preferred_type == 'series':
            tv_results = (tmdb_data or {}).get('tv_results') or []
            if tv_results:
                return f"tmdb:{tv_results[0]['id']}"
        if preferred_type == 'movie':
            movie_results = (tmdb_data or {}).get('movie_results') or []
            if movie_results:
                return f"tmdb:{movie_results[0]['id']}"
        # fallback: first non-empty list
        _id = next((v[0]["id"] for v in tmdb_data.values() if isinstance(v, list) and v), None)
        if _id is None:
            return tmdb_data.get('imdb_id')
        return f"tmdb:{_id}"
    except Exception:
        return tmdb_data.get('imdb_id')


# ---------- Official TMDB API helpers ----------

def _strip_tmdb_prefix(tmdb_id: str | int) -> str:
    s = str(tmdb_id)
    return s.split(':', 1)[1] if ':' in s else s


async def get_tv_details(client: httpx.AsyncClient, series_tmdb_id: str | int, language: str = 'it-IT') -> dict:
    tmdb_numeric = _strip_tmdb_prefix(series_tmdb_id)
    url = f"https://api.themoviedb.org/3/tv/{tmdb_numeric}"
    params = {"language": language, "api_key": TMDB_API_KEY}
    return await fetch_and_retry(client, f"tmdb:tv:{tmdb_numeric}", url, params)


async def get_tv_season(client: httpx.AsyncClient, series_tmdb_id: str | int, season_number: int, language: str = 'it-IT') -> dict:
    tmdb_numeric = _strip_tmdb_prefix(series_tmdb_id)
    url = f"https://api.themoviedb.org/3/tv/{tmdb_numeric}/season/{season_number}"
    params = {"language": language, "api_key": TMDB_API_KEY}
    return await fetch_and_retry(client, f"tmdb:tv:{tmdb_numeric}:s{season_number}", url, params)


async def get_movie_details(client: httpx.AsyncClient, movie_tmdb_id: str | int, language: str = 'it-IT') -> dict:
    tmdb_numeric = _strip_tmdb_prefix(movie_tmdb_id)
    url = f"https://api.themoviedb.org/3/movie/{tmdb_numeric}"
    params = {"language": language, "api_key": TMDB_API_KEY}
    return await fetch_and_retry(client, f"tmdb:movie:{tmdb_numeric}", url, params)


# ---------- Minimal image helpers (backdrop + logo only) ----------

async def get_tv_images(client: httpx.AsyncClient, series_tmdb_id: str | int, include_image_language: str = 'it,it-IT,null,en') -> dict:
    tmdb_numeric = _strip_tmdb_prefix(series_tmdb_id)
    url = f"https://api.themoviedb.org/3/tv/{tmdb_numeric}/images"
    params = {"include_image_language": include_image_language, "api_key": TMDB_API_KEY}
    return await fetch_and_retry(client, f"tmdb:tv:{tmdb_numeric}:images", url, params)


async def get_movie_images(client: httpx.AsyncClient, movie_tmdb_id: str | int, include_image_language: str = 'it,it-IT,null,en') -> dict:
    tmdb_numeric = _strip_tmdb_prefix(movie_tmdb_id)
    url = f"https://api.themoviedb.org/3/movie/{tmdb_numeric}/images"
    params = {"include_image_language": include_image_language, "api_key": TMDB_API_KEY}
    return await fetch_and_retry(client, f"tmdb:movie:{tmdb_numeric}:images", url, params)


def _pick_image_path(items: list, prefer_langs: list[str]) -> tuple[str | None, str | None]:
    if not items:
        return None, None
    for lang in prefer_langs:
        for it in items:
            iso = it.get('iso_639_1')
            if (lang is None and iso is None) or (lang and iso and iso.lower() == lang.lower()):
                return it.get('file_path'), iso
    return items[0].get('file_path'), items[0].get('iso_639_1')


def pick_best_backdrop(images: dict, prefer_langs: list[str] = ['it', 'it-IT', None, 'en', '']) -> tuple[str | None, str | None]:
    backdrops = (images or {}).get('backdrops') or []
    return _pick_image_path(backdrops, prefer_langs)


def pick_best_logo(images: dict, prefer_langs: list[str] = ['it', 'it-IT', None, 'en', '']) -> tuple[str | None, str | None]:
    logos = (images or {}).get('logos') or []
    return _pick_image_path(logos, prefer_langs)

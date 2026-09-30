from cache import Cache
from datetime import timedelta
from collections import defaultdict
import httpx
import os
import asyncio
import json

#from dotenv import load_dotenv
#load_dotenv()

TMDB_POSTER_URL = 'https://image.tmdb.org/t/p/w500'
TMDB_BACK_URL = 'https://image.tmdb.org/t/p/original'
TMDB_API_KEY = os.getenv('TMDB_API_KEY')

TMDB_SEMAPHORES = defaultdict(lambda: asyncio.Semaphore(50))

# Load languages
with open("languages/languages.json", "r", encoding="utf-8") as f:
    LANGUAGES = json.load(f) 

# Cache set
tmp_cache = {}
def open_cache():
    global tmp_cache
    for language in LANGUAGES:
        tmp_cache[language] = Cache(f"./cache/{language}/tmdb/tmp", timedelta(days=7).total_seconds())

def close_cache():
    global tmp_cache
    for language in tmp_cache:
        tmp_cache[language].close()

def get_cache_lenght():
    global tmp_cache
    total_len = 0
    for language in LANGUAGES:
        total_len += tmp_cache[language].get_len()
    return total_len


# Too many requests retry
async def fetch_and_retry(client: httpx.AsyncClient, id: str, url: str, language: str, params={}, max_retries=10) -> dict:
    headers = {
        "accept": "application/json"
    }
    tmdb_api_key = params.get('api_key', None)
    semaphore = TMDB_SEMAPHORES[tmdb_api_key]
    async with semaphore:
        for attempt in range(1, max_retries + 1):
            response = await client.get(url, headers=headers, params=params)

            if response.status_code == 200:
                meta_dict = response.json()

                # Only imdb_id cache save
                if 'tt' in str(id):
                    meta_dict['imdb_id'] = id
                    tmp_cache[language].set(id, meta_dict)

                return meta_dict

            elif response.status_code == 429:
                print(response)
                await asyncio.sleep(1)#(attempt * 2)

            elif response.status_code == 401:
                return {"error": "tmdb-key-error"}

    print('TMDB failed fetch')
    return {}


# Get from external source id
async def get_tmdb_data(client: httpx.AsyncClient, id: str, source: str, language: str, api_key: str) -> dict:
    params = {
        "external_source": source,
        "language": language,
        "api_key": api_key
    }

    url = f"https://api.themoviedb.org/3/find/{id}"
    item = tmp_cache[language].get(id)

    if item != None:
        return item
    else:
        return await fetch_and_retry(client, id, url, language, params)
    

# Get movie detail with cast video and images
async def get_movie_details(client: httpx.AsyncClient, id: str, language: str, api_key: str) -> dict:
    params = {
        "api_key": api_key,
        "language": language,
        "append_to_response": "credits,videos,images",
        "include_image_language": f"{language},null"
    }
    url = f"https://api.themoviedb.org/3/movie/{id}"
    return await fetch_and_retry(client, id, url, language, params=params)


# Get series detail with cast video and images
async def get_series_details(client: httpx.AsyncClient, id: str, language: str, api_key: str) -> dict:
    params = {
        "api_key": api_key,
        "language": language,
        "append_to_response": "external_ids,credits,videos,images",
        "include_image_language": f"{language},null"
    }
    url = f"https://api.themoviedb.org/3/tv/{id}"
    return await fetch_and_retry(client, id, url, language, params=params)


# Get series detail with cast video and images
async def get_season_details(client: httpx.AsyncClient, season_id: str, season_number, language: str, api_key: str) -> dict:
    params = {
        "language": language,
        "append_to_response": "external_ids",
        "api_key": api_key
    }

    url = f"https://api.themoviedb.org/3/tv/{season_id}/season/{season_number}"
    return await fetch_and_retry(client, season_id, url, language, params)

# Converting imdb id to tmdb id
async def convert_imdb_to_tmdb(imdb_id: str, language: str, api_key: str, type: str = None) -> str:

    tmdb_data = tmp_cache[language].get(imdb_id)

    if tmdb_data != None:
        return get_id(tmdb_data, type)
    else:
        async with httpx.AsyncClient(timeout=20) as client:
            tmdb_data = await get_tmdb_data(client, imdb_id, 'imdb_id', language, api_key)
            return get_id(tmdb_data, type)


# Search and parse id (only in the results matching the requested type: movie and tv ids overlap on TMDB)
def get_id(tmdb_data: dict, type: str = None) -> str:
    key = {'movie': 'movie_results', 'series': 'tv_results'}.get(type)
    results = [tmdb_data.get(key)] if key else tmdb_data.values()
    try:
        id = next((v[0]["id"] for v in results if v), None)
    except:
        return tmdb_data['imdb_id']
    else:
        return f"tmdb:{id}"
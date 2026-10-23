from cache import Cache
import api.tmdb as tmdb
import urllib.parse
import asyncio
import httpx
import json
import os

# Load languages
with open("languages/languages.json", "r", encoding="utf-8") as f:
    LANGUAGES = json.load(f) 
with open("languages/lang_flags.json", "r", encoding="utf-8") as f:
    LANGUAGE_FLAGS = json.load(f) 
with open("languages/lang_episode.json", "r", encoding="utf-8") as f:
    EPISODE_TRANSLATIONS = json.load(f) 

# Cache set
translations_cache = {}
def open_cache():
    global translations_cache
    for language in LANGUAGES:
        translations_cache[language] = Cache(f"./cache/{language}/translation/tmp")

def close_cache():
    global translations_cache
    for language in translations_cache:
        translations_cache[language].close()

def get_cache_lenght():
    global translations_cache
    total_len = 0
    for language in LANGUAGES:
        total_len += translations_cache[language].get_len()
    return total_len

# Poster ratings
RATINGS_SERVER = os.getenv('TR_SERVER', 'https://ca6771aaa821-toast-ratings.baby-beamup.club')
# Lingva instance for machine translation; set it empty to disable (original text is kept)
LINGVA_URL = os.getenv('LINGVA_URL', 'https://lingva-translate-azure.vercel.app')



async def translate_with_api(client: httpx.AsyncClient, text: str, language: str, source='en') -> str:

    # An empty cached value is a past failure: retry it
    translation = translations_cache[language].get(text)
    if translation:
        return translation
    if not text or not LINGVA_URL:
        return text

    target = language.split('-')[0]
    api_url = f"{LINGVA_URL}/api/v1/{source}/{target}/{urllib.parse.quote(text)}"
    try:
        response = await client.get(api_url)
        translated_text = response.json().get('translation', '')
    except Exception:
        translated_text = ''

    # Translation service down: keep the original text, don't cache the failure
    if not translated_text:
        return text
    translations_cache[language].set(text, translated_text)
    return translated_text


async def translate_episodes_with_api(client: httpx.AsyncClient, episodes: list[dict], language: str):
    tasks = []

    for episode in episodes:
        tasks.append(translate_with_api(client, episode.get('title', ''), language)),
        tasks.append(translate_with_api(client, episode.get('overview', ''), language))

    translations = await asyncio.gather(*tasks)

    for i, episode in enumerate(episodes):
        episode['title'] = translations[2 * i]
        episode['overview'] = translations[2 * i + 1]

    return episodes


def translate_catalog(original: dict, tmdb_meta: dict, top_stream_poster, toast_ratings, rpdb, rpdb_key, top_stream_key, language: str) -> dict:
    new_catalog = original

    for i, item in enumerate(new_catalog['metas']):
        is_error = tmdb_meta[i].get('error', None)
        if not is_error:
            try:
                type = item['type']
                type_key = 'movie' if type == 'movie' else 'tv'
                detail = tmdb_meta[i][f"{type_key}_results"][0]
            except:
                # Set poster if content not have tmdb informations
                if toast_ratings == '1':
                    if 'tt' in tmdb_meta[i].get('imdb_id', ''):
                        item['poster'] = f"{RATINGS_SERVER}/{item['type']}/get_poster/{language}/{tmdb_meta[i]['imdb_id']}.jpg"
                elif rpdb == '1':
                    if 'tt' in tmdb_meta[i].get('imdb_id', ''):
                        if 't0' in rpdb_key:
                            item['poster'] = f"https://api.ratingposterdb.com/{rpdb_key}/imdb/poster-default/{tmdb_meta[i]['imdb_id']}.jpg"
                        else:
                            item['poster'] = f"https://api.ratingposterdb.com/{rpdb_key}/imdb/poster-default/{tmdb_meta[i]['imdb_id']}.jpg?lang={language.split('-')[0]}"
                elif top_stream_poster == '1':
                    if 'tt' in tmdb_meta[i].get('imdb_id', ''):
                        item['poster'] = f"https://api.top-streaming.stream/{top_stream_key}/imdb/poster-default/{tmdb_meta[i]['imdb_id']}.jpg?lang={language}"

            else:
                try: item['name'] = detail['title'] if type == 'movie' else detail['name']
                except: pass

                try: item['description'] = detail['overview']
                except: pass

                try: item['background'] = tmdb.TMDB_BACK_URL + detail['backdrop_path']
                except: pass

                try: 
                    if toast_ratings == '1':
                        item['poster'] = f"{RATINGS_SERVER}/{item['type']}/get_poster/{language}/{tmdb_meta[i]['imdb_id']}.jpg"
                    elif rpdb == '1':
                        if 't0' in rpdb_key:
                            item['poster'] = f"https://api.ratingposterdb.com/{rpdb_key}/imdb/poster-default/{tmdb_meta[i]['imdb_id']}.jpg"
                        else:
                            item['poster'] = f"https://api.ratingposterdb.com/{rpdb_key}/imdb/poster-default/{tmdb_meta[i]['imdb_id']}.jpg?lang={language.split('-')[0]}"
                    elif top_stream_poster == '1':
                        item['poster'] = f"https://api.top-streaming.stream/{top_stream_key}/imdb/poster-default/{tmdb_meta[i]['imdb_id']}.jpg?lang={language}"
                    else:
                        item['poster'] = tmdb.TMDB_POSTER_URL + detail['poster_path']
                except Exception as e: 
                    print(e)
        # Error
        else:
            item['name'] = 'Invalid TMDB Key'
            item['id'] = 'error:tmdb-key'
            item['poster'] = 'https://i.imgur.com/Zi5UZV3.png'
            item['background'] = None
            item['description'] = 'Invalid TMDB Key'

    return new_catalog


async def translate_episodes(client: httpx.AsyncClient, original_episodes: list[dict], language: str, tmdb_key: str):
    translate_index = []
    tasks = []
    new_episodes = original_episodes

    # Select not translated episodes
    for i, episode in enumerate(original_episodes):
        if 'tvdb_id' in episode:
            tasks.append(tmdb.get_tmdb_data(client, episode['tvdb_id'], "tvdb_id", language, tmdb_key))
            translate_index.append(i)

    translations = await asyncio.gather(*tasks)

    # Translate episodes 
    for i, t_index in enumerate(translate_index):
        try: detail = translations[i][f"tv_episode_results"][0]
        except: pass
        else:
            try: new_episodes[t_index]['name'] = detail['name']
            except: pass
            try: new_episodes[t_index]['overview'] = detail['overview']
            except: pass
            try: new_episodes[t_index]['description'] = detail['overview']
            except: pass
            try: new_episodes[t_index]['thumbnail'] = tmdb.TMDB_BACK_URL + detail['still_path']
            except: pass

    return new_episodes

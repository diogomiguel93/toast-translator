from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from datetime import timedelta, datetime, timezone
from cache import Cache
from anime import kitsu, mal
import meta_merger
import translator
import asyncio
import httpx
import tmdb
import base64
import os

# Settings
translator_version = 'v0.0.9'
FORCE_PREFIX = False
FORCE_META = False
USE_TMDB_ID_META = True
REQUEST_TIMEOUT = 120
COMPATIBILITY_ID = ['tt', 'kitsu', 'mal']

# Cache set
meta_cache = Cache(maxsize=100000, ttl=timedelta(hours=12).total_seconds())
meta_cache.clear()


# Server start
@asynccontextmanager
async def lifespan(app: FastAPI):
    print('Started')
    yield
    print('Shutdown')

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Config CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

stremio_headers = {
    'connection': 'keep-alive', 
    'user-agent': 'Mozilla/5.0 (Windows NT 6.2; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) QtWebEngine/5.15.2 Chrome/83.0.4103.122 Safari/537.36 StremioShell/4.4.168', 
    'accept': '*/*', 
    'origin': 'https://app.strem.io', 
    'sec-fetch-site': 'cross-site', 
    'sec-fetch-mode': 'cors', 
    'sec-fetch-dest': 'empty', 
    'accept-encoding': 'gzip, deflate, br'
}

#tmdb_addon_url = 'https://94c8cb9f702d-tmdb-addon.baby-beamup.club/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D'
#tmdb_madari_url = 'https://tmdb-catalog.madari.media/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D'
#tmdb_elfhosted = 'https://tmdb.elfhosted.com/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D'

tmdb_addons_pool = [
    'https://tmdb.elfhosted.com/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D', # Elfhosted
    'https://94c8cb9f702d-tmdb-addon.baby-beamup.club/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D' # Official
    #'https://tmdb-catalog.madari.media/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D' # Madari
]

tmdb_addon_meta_url = tmdb_addons_pool[0]
cinemeta_url = 'https://v3-cinemeta.strem.io'

def json_response(data):
    response = JSONResponse(data)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Surrogate-Control"] = "no-store"
    return response


@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    response = templates.TemplateResponse("configure.html", {"request": request})
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.get('/{addon_url}/{user_settings}/configure')
async def configure(addon_url):
    addon_url = decode_base64_url(addon_url) + '/configure'
    return RedirectResponse(addon_url)

@app.get('/link_generator', response_class=HTMLResponse)
async def link_generator(request: Request):
    response = templates.TemplateResponse("link_generator.html", {"request": request})
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get('/{addon_url}/{user_settings}/manifest.json')
async def get_manifest(addon_url):
    addon_url = decode_base64_url(addon_url)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(f"{addon_url}/manifest.json")
        manifest = response.json()

    is_translated = manifest.get('translated', False)
    if not is_translated:
        manifest['translated'] = True
        manifest['t_language'] = 'it-IT'
        manifest['name'] += ' 🇮🇹'

        if 'description' in manifest:
            manifest['description'] += f" | Tradotto da Toast Translator. {translator_version}"
        else:
            manifest['description'] = f"Tradotto da Toast Translator. {translator_version}"
    
    if FORCE_PREFIX:
        if 'idPrefixes' in manifest:
            if 'tmdb:' not in manifest['idPrefixes']:
                manifest['idPrefixes'].append('tmdb:')
            if 'tt' not in manifest['idPrefixes']:
                manifest['idPrefixes'].append('tt')

    if FORCE_META:
        if 'meta' not in manifest['resources']:
            manifest['resources'].append('meta')

    return json_response(manifest)


@app.get('/{addon_url}/{user_settings}/catalog/{type}/{path:path}')
async def get_catalog(response: Response, addon_url, type: str, user_settings: str, path: str):
    # Cinemeta last-videos
    if 'last-videos' in path:
        return RedirectResponse(f"{cinemeta_url}/catalog/{type}/{path}")
    
    user_settings = parse_user_settings(user_settings)
    addon_url = decode_base64_url(addon_url)

    async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(f"{addon_url}/catalog/{type}/{path}")

        try:
            catalog = response.json()
        except:
            print(response.text)
            return json_response({})

        if 'metas' in catalog:
            if type == 'anime':
                await remove_duplicates(catalog)
            tasks = [
                tmdb.get_tmdb_data(client, item.get('imdb_id', item.get('id')), "imdb_id") for item in catalog['metas']
            ]
            tmdb_details = await asyncio.gather(*tasks)
        else:
            return json_response({})

    new_catalog = translator.translate_catalog(catalog, tmdb_details, user_settings['sp'], user_settings['tr'])
    return json_response(new_catalog)


@app.get('/{addon_url}/{user_settings}/meta/{type}/{id}.json')
async def get_meta(request: Request,response: Response, addon_url, user_settings: str, type: str, id: str):
    headers = dict(request.headers)
    del headers['host']
    addon_url = decode_base64_url(addon_url)
    settings = parse_user_settings(user_settings)
    global tmdb_addon_meta_url
    async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT) as client:

        # Get from cache
        meta = meta_cache.get(id)

        # Return cached meta
        if meta != None:
            return json_response(meta)

        # Not in cache
        else:
            # Handle imdb ids
            if 'tt' in id:
                # Always try official TMDB meta first, then fallback to TMDB addons; merge with Cinemeta as before.
                async def _official_tmdb_meta_flow() -> dict | None:
                    try:
                        print(f"[META][TMDB-OFFICIAL] Start for {id} ({type})")
                        preferred = 'series' if type == 'series' else 'movie'
                        tmdb_id = await tmdb.convert_imdb_to_tmdb(id, preferred_type=preferred, bypass_cache=True)
                        if type == 'series':
                            details = await tmdb.get_tv_details(client, tmdb_id, language='it-IT')
                            seasons = sorted([s.get('season_number') for s in (details.get('seasons') or []) if s.get('season_number') and s.get('season_number') > 0])
                            # Carica tutte le stagioni per allinearsi al comportamento precedente degli addon TMDB
                            tasks = [tmdb.get_tv_season(client, tmdb_id, sn, language='it-IT') for sn in seasons]
                            seasons_data = await asyncio.gather(*tasks)

                            def to_iso_z(d):
                                return f"{d}T00:00:00.000Z" if d else None
                            def is_future(d_str):
                                try:
                                    return datetime.strptime(d_str, "%Y-%m-%d").date() > datetime.utcnow().date()
                                except Exception:
                                    return False

                            videos = []
                            upcoming_count = 0
                            for sdata in seasons_data:
                                sn = sdata.get('season_number')
                                for e in (sdata.get('episodes') or []):
                                    v = {
                                        'id': f"{id}:{sn}:{e.get('episode_number')}",
                                        'season': sn,
                                        'episode': e.get('episode_number'),
                                        'name': e.get('name'),
                                        'overview': e.get('overview'),
                                        'description': e.get('overview'),
                                        'thumbnail': (tmdb.TMDB_BACK_URL + e['still_path']) if e.get('still_path') else None,
                                        'firstAired': to_iso_z(e.get('air_date')),
                                        'released': to_iso_z(e.get('air_date')),
                                        'rating': e.get('vote_average')
                                    }
                                    if e.get('air_date') and is_future(e.get('air_date')):
                                        v['releaseInfo'] = 'Prossimamente'
                                        upcoming_count += 1
                                    videos.append(v)

                            # Costruisci meta includendo solo i campi valorizzati per non sovrascrivere Cinemeta con vuoti
                            meta_obj = {
                                'meta': {
                                    'id': id,
                                    'type': 'series',
                                    'imdb_id': id,
                                    'videos': sorted(videos, key=lambda v: (v.get('season', 0), v.get('episode', 0)))
                                }
                            }
                            series_name = details.get('name') or details.get('original_name')
                            if series_name:
                                meta_obj['meta']['name'] = series_name
                            if details.get('overview'):
                                meta_obj['meta']['description'] = details.get('overview')
                            if upcoming_count > 0:
                                meta_obj['meta'].setdefault('behaviorHints', {})
                                meta_obj['meta']['behaviorHints']['hasScheduledVideos'] = True
                            return meta_obj
                        else:
                            movie_details = await tmdb.get_movie_details(client, tmdb_id, language='it-IT')
                            def to_iso_z(d):
                                return f"{d}T00:00:00.000Z" if d else None
                            meta_obj = {
                                'meta': {
                                    'id': id,
                                    'type': 'movie',
                                    'imdb_id': id,
                                    'videos': []
                                }
                            }
                            movie_name = movie_details.get('title') or movie_details.get('name') or movie_details.get('original_title') or movie_details.get('original_name')
                            if movie_name:
                                meta_obj['meta']['name'] = movie_name
                            if movie_details.get('overview'):
                                meta_obj['meta']['description'] = movie_details.get('overview')
                            if movie_details.get('release_date'):
                                meta_obj['meta']['released'] = to_iso_z(movie_details.get('release_date'))
                                meta_obj['meta']['firstAired'] = to_iso_z(movie_details.get('release_date'))
                            return meta_obj
                    except Exception:
                        print(f"[META][TMDB-OFFICIAL] Failed for {id}")
                        return None

                # Get Cinemeta as before
                cinemeta_resp = await client.get(f"{cinemeta_url}/meta/{type}/{id}.json")
                cinemeta_meta = {}
                if cinemeta_resp.status_code == 200:
                    try:
                        cinemeta_meta = cinemeta_resp.json()
                    except Exception:
                        cinemeta_meta = {}

                tmdb_meta = await _official_tmdb_meta_flow() or {}
                if tmdb_meta.get('meta'):
                    print(f"[META] Using official TMDB meta for {id}")
                else:
                    print(f"[META] Official TMDB meta missing for {id}, fallback to TMDB addons")

                # Fallback to TMDB addons when official fails
                if not tmdb_meta or len(tmdb_meta.get('meta', [])) == 0:
                    tmdb_id = await tmdb.convert_imdb_to_tmdb(id)
                    tasks = [
                        client.get(f"{tmdb_addon_meta_url}/meta/{type}/{tmdb_id}.json")
                    ]
                    metas = await asyncio.gather(*tasks)
                    # TMDB addon retry and switch addon
                    for retry in range(6):
                        if metas[0].status_code == 200:
                            try:
                                parsed = metas[0].json()
                            except Exception:
                                parsed = {}
                            if parsed.get('meta'):
                                tmdb_meta = parsed
                                break
                        else:
                            index = tmdb_addons_pool.index(tmdb_addon_meta_url)
                            tmdb_addon_meta_url = tmdb_addons_pool[(index + 1) % len(tmdb_addons_pool)]
                            print(f"[META][TMDB-ADDON] Switch -> {tmdb_addon_meta_url}")
                            metas[0] = await client.get(f"{tmdb_addon_meta_url}/meta/{type}/{tmdb_id}.json")
                            if metas[0].status_code == 200:
                                try:
                                    parsed = metas[0].json()
                                except Exception:
                                    parsed = {}
                                if parsed.get('meta'):
                                    tmdb_meta = parsed
                                    print(f"[META][TMDB-ADDON] Taken from {tmdb_addon_meta_url} for {id}")
                                    break

                # Proceed with original merge logic using tmdb_meta + cinemeta_meta
                if len(tmdb_meta.get('meta', [])) > 0:
                    # Not merge anime
                    if id not in kitsu.imdb_ids_map:
                        tasks = []
                        meta, merged_videos = meta_merger.merge(tmdb_meta, cinemeta_meta)
                        # No forced name override; merge handles fields
                        tmdb_description = tmdb_meta['meta'].get('description', '')
                        
                        if tmdb_description == '':
                            _desc = meta['meta'].get('description', '')
                            if _desc:
                                tasks.append(translator.translate_with_api(client, _desc))

                        if type == 'series' and (len(meta['meta']['videos']) < len(merged_videos)):
                            tasks.append(translator.translate_episodes(client, merged_videos))

                        translated_tasks = await asyncio.gather(*tasks)
                        for task in translated_tasks:
                            if isinstance(task, list):
                                meta['meta']['videos'] = task
                            elif isinstance(task, str):
                                meta['meta']['description'] = task
                        # Ensure upcoming flags present after merge/translation
                        if type == 'series':
                            u = _mark_upcoming(meta['meta'].get('videos', []))
                            if u > 0:
                                meta['meta'].setdefault('behaviorHints', {})
                                meta['meta']['behaviorHints']['hasScheduledVideos'] = True
                    else:
                        meta = tmdb_meta

                # Empty tmdb_data
                else:
                    if len(cinemeta_meta.get('meta', [])) > 0:
                        meta = cinemeta_meta
                        description = meta['meta'].get('description', '')
                        
                        if type == 'series':
                            tasks = []
                            # Translate description only if present
                            if description:
                                tasks.append(translator.translate_with_api(client, description))
                            tasks.append(translator.translate_episodes(client, meta['meta']['videos']))
                            results = await asyncio.gather(*tasks)
                            if description:
                                description = results[0]
                                episodes = results[1]
                            else:
                                episodes = results[0]
                            meta['meta']['videos'] = episodes
                            u = _mark_upcoming(meta['meta'].get('videos', []))
                            if u > 0:
                                meta['meta'].setdefault('behaviorHints', {})
                                meta['meta']['behaviorHints']['hasScheduledVideos'] = True
                                pass

                        elif type == 'movie':
                            if description:
                                description = await translator.translate_with_api(client, description)

                        meta['meta']['description'] = description
                    
                    # Empty cinemeta and tmdb return empty meta
                    else:
                        return json_response({})
                    
                
            # Handle kitsu and mal ids
            elif 'kitsu' in id or 'mal' in id:
                # Try convert Kitsu/MAL to IMDb
                if 'kitsu' in id:
                    imdb_id, is_converted = await kitsu.convert_to_imdb(id, type)
                else:
                    imdb_id, is_converted = await mal.convert_to_imdb(id.replace('_',':'), type)

                if is_converted:
                    # Official TMDB API first (no merge for anime); fallback to TMDB addons; final fallback Kitsu addon
                    meta = {}
                    try:
                        preferred = 'series' if type == 'series' else 'movie'
                        tmdb_id = await tmdb.convert_imdb_to_tmdb(imdb_id, preferred_type=preferred, bypass_cache=True)
                        if type == 'series':
                            details = await tmdb.get_tv_details(client, tmdb_id, language='it-IT')
                            seasons = sorted([s.get('season_number') for s in (details.get('seasons') or []) if s.get('season_number') and s.get('season_number') > 0])
                            tasks = [tmdb.get_tv_season(client, tmdb_id, sn, language='it-IT') for sn in seasons]
                            seasons_data = await asyncio.gather(*tasks)

                            def to_iso_z(d):
                                return f"{d}T00:00:00.000Z" if d else None
                            def is_future(d_str):
                                try:
                                    return datetime.strptime(d_str, "%Y-%m-%d").date() > datetime.utcnow().date()
                                except Exception:
                                    return False

                            videos = []
                            upcoming_count = 0
                            for sdata in seasons_data:
                                sn = sdata.get('season_number')
                                for e in (sdata.get('episodes') or []):
                                    v = {
                                        'id': f"{imdb_id}:{sn}:{e.get('episode_number')}",
                                        'season': sn,
                                        'episode': e.get('episode_number'),
                                        'name': e.get('name'),
                                        'overview': e.get('overview'),
                                        'description': e.get('overview'),
                                        'thumbnail': (tmdb.TMDB_BACK_URL + e['still_path']) if e.get('still_path') else None,
                                        'firstAired': to_iso_z(e.get('air_date')),
                                        'released': to_iso_z(e.get('air_date')),
                                        'rating': e.get('vote_average')
                                    }
                                    if e.get('air_date') and is_future(e.get('air_date')):
                                        v['releaseInfo'] = 'Prossimamente'
                                        upcoming_count += 1
                                    videos.append(v)

                            meta_obj = {
                                'meta': {
                                    'id': id,
                                    'type': 'series',
                                    'imdb_id': imdb_id,
                                    'videos': sorted(videos, key=lambda v: (v.get('season', 0), v.get('episode', 0)))
                                }
                            }
                            series_name = details.get('name') or details.get('original_name')
                            if series_name:
                                meta_obj['meta']['name'] = series_name
                            if details.get('overview'):
                                meta_obj['meta']['description'] = details.get('overview')
                            if upcoming_count > 0:
                                meta_obj['meta'].setdefault('behaviorHints', {})
                                meta_obj['meta']['behaviorHints']['hasScheduledVideos'] = True
                            meta = meta_obj
                        else:
                            movie_details = await tmdb.get_movie_details(client, tmdb_id, language='it-IT')
                            def to_iso_z(d):
                                return f"{d}T00:00:00.000Z" if d else None
                            meta_obj = {
                                'meta': {
                                    'id': id,
                                    'type': 'movie',
                                    'imdb_id': imdb_id,
                                    'videos': []
                                }
                            }
                            movie_name = movie_details.get('title') or movie_details.get('name') or movie_details.get('original_title') or movie_details.get('original_name')
                            if movie_name:
                                meta_obj['meta']['name'] = movie_name
                            if movie_details.get('overview'):
                                meta_obj['meta']['description'] = movie_details.get('overview')
                            if movie_details.get('release_date'):
                                meta_obj['meta']['released'] = to_iso_z(movie_details.get('release_date'))
                                meta_obj['meta']['firstAired'] = to_iso_z(movie_details.get('release_date'))
                            meta = meta_obj
                    except Exception:
                        meta = {}

                    # Fallback: TMDB addons if official failed
                    if not meta or not meta.get('meta'):
                        tmdb_id = await tmdb.convert_imdb_to_tmdb(imdb_id)
                        for retry in range(6):
                            response = await client.get(f"{tmdb_addon_meta_url}/meta/{type}/{tmdb_id}.json")
                            if response.status_code == 200:
                                try:
                                    parsed = response.json()
                                except Exception:
                                    parsed = {}
                                if parsed.get('meta'):
                                    meta = parsed
                                    break
                            # Loop addon pool
                            index = tmdb_addons_pool.index(tmdb_addon_meta_url)
                            tmdb_addon_meta_url = tmdb_addons_pool[(index + 1) % len(tmdb_addons_pool)]
                            print(f"[META][TMDB-ADDON] Switch -> {tmdb_addon_meta_url}")

                    # Final fallback: Kitsu addon
                    if not meta or not meta.get('meta'):
                        response = await client.get(f"{kitsu.kitsu_addon_url}/meta/{type}/{id.replace(':','%3A')}.json")
                        meta = response.json()

                    # Anime-specific post-processing
                    if len(meta.get('meta', {})) > 0:
                        if type == 'movie':
                            meta.setdefault('meta', {}).setdefault('behaviorHints', {})
                            meta['meta']['behaviorHints']['defaultVideoId'] = id
                        elif type == 'series':
                            videos = kitsu.parse_meta_videos(meta['meta'].get('videos', []), imdb_id)
                            meta['meta']['videos'] = videos
                        if type == 'series':
                            u = _mark_upcoming(meta['meta'].get('videos', []))
                            if u > 0:
                                meta['meta'].setdefault('behaviorHints', {})
                                meta['meta']['behaviorHints']['hasScheduledVideos'] = True
                else:
                    # Get meta from kitsu addon if conversion failed
                    response = await client.get(f"{kitsu.kitsu_addon_url}/meta/{type}/{id.replace(':','%3A')}.json")
                    meta = response.json()

            # Not compatible id -> redirect to original addon
            else:
                return RedirectResponse(f"{addon_url}/meta/{type}/{id}.json")


            meta['meta']['id'] = id
            # Remove generic image fallback and Toast Ratings poster forcing
            meta_cache.set(id, meta)
            return json_response(meta)


# Subs redirect
@app.get('/{addon_url}/{user_settings}/subtitles/{path:path}')
async def get_subs(addon_url, path: str):
    addon_url = decode_base64_url(addon_url)
    return RedirectResponse(f"{addon_url}/subtitles/{path}")

# Stream redirect
@app.get('/{addon_url}/{user_settings}/stream/{path:path}')
async def get_subs(addon_url, path: str):
    addon_url = decode_base64_url(addon_url)
    return RedirectResponse(f"{addon_url}/stream/{path}")


def decode_base64_url(encoded_url):
    padding = '=' * (-len(encoded_url) % 4)
    encoded_url += padding
    decoded_bytes = base64.b64decode(encoded_url)
    return decoded_bytes.decode('utf-8')


# Helpers: upcoming flagging
def _is_future_date_str(d: str | None) -> bool:
    if not d:
        return False
    try:
        # supports 'YYYY-MM-DD' and ISO with time
        date_part = d[:10]
        return datetime.strptime(date_part, "%Y-%m-%d").date() > datetime.utcnow().date()
    except Exception:
        return False


def _mark_upcoming(videos: list[dict]) -> int:
    """Mark upcoming episodes with releaseInfo='Prossimamente' and flags; return count."""
    count = 0
    for v in videos or []:
        d = v.get('firstAired') or v.get('released')
        if _is_future_date_str(d):
            v['releaseInfo'] = 'Prossimamente'
            v['isUpcoming'] = True
            v['upcoming'] = True
            count += 1
    return count


# Anime only
async def remove_duplicates(catalog) -> None:
    unique_items = []
    seen_ids = set()
    
    for item in catalog['metas']:

        if 'kitsu' in item['id']:
            item['imdb_id'], is_converted = await kitsu.convert_to_imdb(item['id'], item['type'])

        elif 'mal_' in item['id']:
            item['imdb_id'], is_converted = await mal.convert_to_imdb(item['id'].replace('_',':'), item['type'])

        if item['imdb_id'] not in seen_ids:
            unique_items.append(item)
            seen_ids.add(item['imdb_id'])

    catalog['metas'] = unique_items


def parse_user_settings(user_settings: str) -> dict:
    settings = user_settings.split(',')
    _user_settings = {}

    for setting in settings:
        key, value = setting.split('=')
        _user_settings[key] = value
    
    return _user_settings


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

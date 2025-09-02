from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from datetime import timedelta
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
translator_version = 'v0.0.8'
FORCE_PREFIX = False
FORCE_META = False
USE_TMDB_ID_META = True
REQUEST_TIMEOUT = 120
COMPATIBILITY_ID = ['tt', 'kitsu', 'mal']
OFFICIAL_TMDB_ONLY = os.getenv('OFFICIAL_TMDB_ONLY', '1') == '1'

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
    'https://94c8cb9f702d-tmdb-addon.baby-beamup.club/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D', # Official
    'https://tmdb-catalog.madari.media/%7B%22provide_imdbId%22%3A%22true%22%2C%22language%22%3A%22it-IT%22%7D' # Madari
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
        resp = await client.get(f"{addon_url}/catalog/{type}/{path}")

        try:
            catalog = resp.json()
        except:
            print(resp.text)
            return {}

        if 'metas' in catalog:
            if type == 'anime':
                await remove_duplicates(catalog)
            tasks = [
                tmdb.get_tmdb_data(client, item.get('imdb_id', item.get('id')), "imdb_id") for item in catalog['metas']
            ]
            tmdb_details = await asyncio.gather(*tasks)
        else:
            return {}

    new_catalog = translator.translate_catalog(catalog, tmdb_details, user_settings['sp'], user_settings['tr'])
    return json_response(new_catalog)


@app.get('/{addon_url}/{user_settings}/meta/{type}/{id}.json')
async def get_meta(request: Request, response: Response, addon_url, type: str, id: str):
    headers = dict(request.headers)
    del headers['host']
    addon_url = decode_base64_url(addon_url)
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
                tmdb_id = await tmdb.convert_imdb_to_tmdb(id)
                # Try all TMDB addon candidates (per-request) and pick the best
                async def get_best_tmdb_meta() -> dict:
                    # Prefer current default first, then the rest
                    ordered = [tmdb_addon_meta_url] + [u for u in tmdb_addons_pool if u != tmdb_addon_meta_url]
                    best = {}
                    best_len = -1
                    for base in ordered:
                        r = await client.get(f"{base}/meta/{type}/{tmdb_id}.json")
                        if r.status_code != 200:
                            continue
                        try:
                            m = r.json()
                        except Exception:
                            continue
                        if not m or 'meta' not in m:
                            continue
                        # For series prefer longest videos list; for movies any valid
                        if type == 'series':
                            vlen = len(m['meta'].get('videos', []) or [])
                            if vlen > best_len:
                                best, best_len = m, vlen
                        else:
                            return m
                    return best

                tmdb_meta = await get_best_tmdb_meta()
                cinemeta_resp = await client.get(f"{cinemeta_url}/meta/{type}/{id}.json")
                cinemeta_meta = cinemeta_resp.json() if cinemeta_resp.status_code == 200 else {}
                
                # Not empty tmdb meta
                if len(tmdb_meta.get('meta', [])) > 0:
                    # If series, enrich/replace episodes using TMDB official API (it-IT)
                    if type == 'series':
                        try:
                            videos = tmdb_meta['meta'].get('videos', []) or []
                            base_imdb = tmdb_meta['meta'].get('imdb_id', id)
                            def to_iso_z(d: str | None) -> str | None:
                                if not d:
                                    return None
                                return f"{d}T00:00:00.000Z"

                            if OFFICIAL_TMDB_ONLY:
                                # Build ALL seasons from official TMDB API
                                details = await tmdb.get_tv_details(client, tmdb_id, language='it-IT')
                                seasons = [s.get('season_number') for s in (details.get('seasons') or []) if s.get('season_number') and s.get('season_number') > 0]
                                # Fetch all seasons in parallel
                                tasks = [tmdb.get_tv_season(client, tmdb_id, sn, language='it-IT') for sn in seasons]
                                seasons_data = await asyncio.gather(*tasks)
                                rebuilt_all = []
                                for sdata in seasons_data:
                                    sn = sdata.get('season_number')
                                    for e in sdata.get('episodes', []) or []:
                                        rebuilt_all.append({
                                            'id': f"{base_imdb}:{sn}:{e.get('episode_number')}",
                                            'season': sn,
                                            'episode': e.get('episode_number'),
                                            'name': e.get('name'),
                                            'overview': e.get('overview'),
                                            'description': e.get('overview'),
                                            'thumbnail': (tmdb.TMDB_BACK_URL + e['still_path']) if e.get('still_path') else None,
                                            'firstAired': to_iso_z(e.get('air_date')),
                                            'released': to_iso_z(e.get('air_date')),
                                            'rating': e.get('vote_average')
                                        })
                                # Stable sort by season then episode
                                tmdb_meta['meta']['videos'] = sorted(rebuilt_all, key=lambda v: (v.get('season', 0), v.get('episode', 0)))
                            else:
                                # Augment only highest season
                                if videos:
                                    highest_season = max(v.get('season', 0) for v in videos)
                                    season_data = await tmdb.get_tv_season(client, tmdb_id, highest_season, language='it-IT')
                                    tmdb_eps = season_data.get('episodes', [])
                                    existing = {(v.get('season'), v.get('episode')) for v in videos}
                                    new_eps = []
                                    for e in tmdb_eps:
                                        key = (highest_season, e.get('episode_number'))
                                        if key in existing:
                                            continue
                                        new_eps.append({
                                            'id': f"{base_imdb}:{highest_season}:{e.get('episode_number')}",
                                            'season': highest_season,
                                            'episode': e.get('episode_number'),
                                            'name': e.get('name'),
                                            'overview': e.get('overview'),
                                            'description': e.get('overview'),
                                            'thumbnail': (tmdb.TMDB_BACK_URL + e['still_path']) if e.get('still_path') else None,
                                            'firstAired': to_iso_z(e.get('air_date')),
                                            'released': to_iso_z(e.get('air_date')),
                                            'rating': e.get('vote_average')
                                        })
                                    if new_eps:
                                        tmdb_meta['meta']['videos'].extend(new_eps)
                        except Exception as _e:
                            # Non-bloccante: in caso di errori, proseguiamo con i dati disponibili
                            pass
                    # Not merge anime
                    if id not in kitsu.imdb_ids_map:
                        tasks = []
                        meta, merged_videos = meta_merger.merge(tmdb_meta, cinemeta_meta)
                        tmdb_description = tmdb_meta['meta'].get('description', '')
                        
                        if tmdb_description == '':
                            tasks.append(translator.translate_with_api(client, meta['meta']['description']))

                        if type == 'series' and (len(meta['meta']['videos']) < len(merged_videos)):
                            tasks.append(translator.translate_episodes(client, merged_videos))

                        translated_tasks = await asyncio.gather(*tasks)
                        for task in translated_tasks:
                            if isinstance(task, list):
                                meta['meta']['videos'] = task
                            elif isinstance(task, str):
                                meta['meta']['description'] = task
                    else:
                        meta = tmdb_meta

                # Empty or weak tmdb_data
                else:
                    if len(cinemeta_meta.get('meta', [])) > 0:
                        meta = cinemeta_meta
                        description = meta['meta'].get('description', '')
                        
                        if type == 'series':
                            tasks = [
                                translator.translate_with_api(client, description),
                                translator.translate_episodes(client, meta['meta']['videos'])
                            ]
                            description, episodes = await asyncio.gather(*tasks)
                            meta['meta']['videos'] = episodes
                            meta['meta']['videos'] = await translator.translate_episodes(client, meta['meta']['videos'])

                        elif type == 'movie':
                            description = await translator.translate_with_api(client, description)

                        meta['meta']['description'] = description
                    
                    # Empty cinemeta and tmdb return empty meta
                    else:
                        return {}
                    
                
            # Handle kitsu and mal ids
            elif 'kitsu' in id or 'mal' in id:
                # Try convert kitsu to imdb
                if 'kitsu' in id:
                    imdb_id, is_converted = await kitsu.convert_to_imdb(id, type)
                else:
                    imdb_id, is_converted = await mal.convert_to_imdb(id.replace('_',':'), type)

                if is_converted:
                    tmdb_id = await tmdb.convert_imdb_to_tmdb(imdb_id)
                    # Try all TMDB addon candidates (per-request)
                    meta = {}
                    ordered = [tmdb_addon_meta_url] + [u for u in tmdb_addons_pool if u != tmdb_addon_meta_url]
                    for base in ordered:
                        r = await client.get(f"{base}/meta/{type}/{tmdb_id}.json")
                        if r.status_code == 200:
                            try:
                                meta = r.json()
                            except Exception:
                                meta = {}
                            if meta:
                                break

                    if len(meta['meta']) > 0:
                        if type == 'movie':
                            meta['meta']['behaviorHints']['defaultVideoId'] = id
                        elif type == 'series':
                            videos = kitsu.parse_meta_videos(meta['meta']['videos'], imdb_id)
                            meta['meta']['videos'] = videos
                    else:
                        # Get meta from kitsu addon
                        response = await client.get(f"{kitsu.kitsu_addon_url}/meta/{type}/{id.replace(':','%3A')}.json")
                        meta = response.json()
                else:
                    # Get meta from kitsu addon
                    response = await client.get(f"{kitsu.kitsu_addon_url}/meta/{type}/{id.replace(':','%3A')}.json")
                    meta = response.json()

            # Not compatible id -> redirect to original addon
            else:
                return RedirectResponse(f"{addon_url}/meta/{type}/{id}.json")


            meta['meta']['id'] = id
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

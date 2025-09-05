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
translator_version = 'v0.1.1'
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
                            images = await tmdb.get_tv_images(client, tmdb_id)
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
                                        # Etichetta in italiano per il badge
                                        v['releaseInfo'] = 'Prossimamente'
                                        upcoming_count += 1
                                        print(f"[META][UPCOMING] {id} S{sn:02d}E{e.get('episode_number')} -> {e.get('air_date')}")
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
                            # immagini: preferisci italiane
                            p_path, p_lang = tmdb.pick_best_poster(images)
                            b_path, b_lang = tmdb.pick_best_backdrop(images)
                            if p_path:
                                meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p_path)
                            if b_path:
                                meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b_path)
                            # logo
                            l_path, l_lang = tmdb.pick_best_logo(images)
                            if l_path:
                                meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l_path)
                            # EN images fallback if any missing
                            if not meta_obj['meta'].get('poster') or not meta_obj['meta'].get('background') or not meta_obj['meta'].get('logo'):
                                images_en = await tmdb.get_tv_images(client, tmdb_id, include_image_language='en,en-EN,null,en')
                                if not meta_obj['meta'].get('poster'):
                                    p2, _ = tmdb.pick_best_poster(images_en)
                                    if p2:
                                        meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p2)
                                if not meta_obj['meta'].get('background'):
                                    b2, _ = tmdb.pick_best_backdrop(images_en)
                                    if b2:
                                        meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b2)
                                if not meta_obj['meta'].get('logo'):
                                    l2, _ = tmdb.pick_best_logo(images_en)
                                    if l2:
                                        meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l2)
                            print(f"[META][IMG] {id} chosen poster_lang={p_lang} backdrop_lang={b_lang}")
                            if upcoming_count > 0:
                                meta_obj['meta'].setdefault('behaviorHints', {})
                                meta_obj['meta']['behaviorHints']['hasScheduledVideos'] = True
                                print(f"[META][UPCOMING][FLAG] hasScheduledVideos=True for {id}")
                            print(f"[META][TMDB-OFFICIAL] Series {id}: seasons={len(seasons)} episodes={len(videos)} upcoming={upcoming_count}")
                            return meta_obj
                        else:
                            movie_details = await tmdb.get_movie_details(client, tmdb_id, language='it-IT')
                            movie_images = await tmdb.get_movie_images(client, tmdb_id)
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
                            p_path, p_lang = tmdb.pick_best_poster(movie_images)
                            b_path, b_lang = tmdb.pick_best_backdrop(movie_images)
                            if p_path:
                                meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p_path)
                            if b_path:
                                meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b_path)
                            l_path, l_lang = tmdb.pick_best_logo(movie_images)
                            if l_path:
                                meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l_path)
                            # EN images fallback if any missing
                            if not meta_obj['meta'].get('poster') or not meta_obj['meta'].get('background') or not meta_obj['meta'].get('logo'):
                                movie_images_en = await tmdb.get_movie_images(client, tmdb_id, include_image_language='en,en-EN,null,en')
                                if not meta_obj['meta'].get('poster'):
                                    p2, _ = tmdb.pick_best_poster(movie_images_en)
                                    if p2:
                                        meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p2)
                                if not meta_obj['meta'].get('background'):
                                    b2, _ = tmdb.pick_best_backdrop(movie_images_en)
                                    if b2:
                                        meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b2)
                                if not meta_obj['meta'].get('logo'):
                                    l2, _ = tmdb.pick_best_logo(movie_images_en)
                                    if l2:
                                        meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l2)
                            print(f"[META][IMG] {id} chosen poster_lang={p_lang} backdrop_lang={b_lang}")
                            if movie_details.get('release_date'):
                                meta_obj['meta']['released'] = to_iso_z(movie_details.get('release_date'))
                                meta_obj['meta']['firstAired'] = to_iso_z(movie_details.get('release_date'))
                            print(f"[META][TMDB-OFFICIAL] Movie {id}: poster={'ok' if meta_obj['meta'].get('poster') else 'no'} background={'ok' if meta_obj['meta'].get('background') else 'no'}")
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
                        # Log sorgente poster/background
                        try:
                            tmdb_has_poster = bool(tmdb_meta.get('meta', {}).get('poster'))
                            cm_has_poster = bool((cinemeta_meta.get('meta') or {}).get('poster'))
                            poster_src = 'TMDB' if tmdb_has_poster else ('Cinemeta' if cm_has_poster else 'none')
                            tmdb_has_bg = bool(tmdb_meta.get('meta', {}).get('background'))
                            cm_has_bg = bool((cinemeta_meta.get('meta') or {}).get('background'))
                            bg_src = 'TMDB' if tmdb_has_bg else ('Cinemeta' if cm_has_bg else 'none')
                            print(f"[META][IMG] {id} poster={poster_src} background={bg_src}")
                        except Exception:
                            pass
                        # Assicura che il titolo italiano da TMDB prevalga sul titolo di Cinemeta
                        tmdb_name = tmdb_meta['meta'].get('name')
                        if tmdb_name:
                            meta['meta']['name'] = tmdb_name
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
                                print(f"[META][UPCOMING][FLAG] hasScheduledVideos=True for {id} (merged)")
                    else:
                        meta = tmdb_meta
                        # Anime: fallback al logo da Cinemeta se TMDB non lo fornisce
                        try:
                            if not meta['meta'].get('logo') and (cinemeta_meta.get('meta') or {}).get('logo'):
                                meta['meta']['logo'] = cinemeta_meta['meta']['logo']
                                print(f"[META][IMG][FALLBACK] Logo from Cinemeta for {id}")
                        except Exception:
                            pass

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
                                print(f"[META][UPCOMING][FLAG] hasScheduledVideos=True for {id} (cinemeta)")

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
                            images = await tmdb.get_tv_images(client, tmdb_id)
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
                                        print(f"[META][UPCOMING] {imdb_id} S{sn:02d}E{e.get('episode_number')} -> {e.get('air_date')}")
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
                            p_path, p_lang = tmdb.pick_best_poster(images)
                            b_path, b_lang = tmdb.pick_best_backdrop(images)
                            if p_path:
                                meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p_path)
                            if b_path:
                                meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b_path)
                            l_path, l_lang = tmdb.pick_best_logo(images)
                            if l_path:
                                meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l_path)
                            # EN images fallback if any missing
                            if not meta_obj['meta'].get('poster') or not meta_obj['meta'].get('background') or not meta_obj['meta'].get('logo'):
                                images_en = await tmdb.get_tv_images(client, tmdb_id, include_image_language='en,en-EN,null,en')
                                if not meta_obj['meta'].get('poster'):
                                    p2, _ = tmdb.pick_best_poster(images_en)
                                    if p2:
                                        meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p2)
                                if not meta_obj['meta'].get('background'):
                                    b2, _ = tmdb.pick_best_backdrop(images_en)
                                    if b2:
                                        meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b2)
                                if not meta_obj['meta'].get('logo'):
                                    l2, _ = tmdb.pick_best_logo(images_en)
                                    if l2:
                                        meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l2)
                            print(f"[META][IMG] {imdb_id} chosen poster_lang={p_lang} backdrop_lang={b_lang}")
                            if upcoming_count > 0:
                                meta_obj['meta'].setdefault('behaviorHints', {})
                                meta_obj['meta']['behaviorHints']['hasScheduledVideos'] = True
                                print(f"[META][UPCOMING][FLAG] hasScheduledVideos=True for {imdb_id}")
                            print(f"[META][TMDB-OFFICIAL] Anime series {imdb_id}: episodes={len(videos)} upcoming={upcoming_count}")
                            meta = meta_obj
                        else:
                            movie_details = await tmdb.get_movie_details(client, tmdb_id, language='it-IT')
                            movie_images = await tmdb.get_movie_images(client, tmdb_id)
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
                            p_path, p_lang = tmdb.pick_best_poster(movie_images)
                            b_path, b_lang = tmdb.pick_best_backdrop(movie_images)
                            if p_path:
                                meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p_path)
                            if b_path:
                                meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b_path)
                            l_path, l_lang = tmdb.pick_best_logo(movie_images)
                            if l_path:
                                meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l_path)
                            # EN images fallback if any missing
                            if not meta_obj['meta'].get('poster') or not meta_obj['meta'].get('background') or not meta_obj['meta'].get('logo'):
                                movie_images_en = await tmdb.get_movie_images(client, tmdb_id, include_image_language='en,en-EN,null,en')
                                if not meta_obj['meta'].get('poster'):
                                    p2, _ = tmdb.pick_best_poster(movie_images_en)
                                    if p2:
                                        meta_obj['meta']['poster'] = (tmdb.TMDB_POSTER_URL + p2)
                                if not meta_obj['meta'].get('background'):
                                    b2, _ = tmdb.pick_best_backdrop(movie_images_en)
                                    if b2:
                                        meta_obj['meta']['background'] = (tmdb.TMDB_BACK_URL + b2)
                                if not meta_obj['meta'].get('logo'):
                                    l2, _ = tmdb.pick_best_logo(movie_images_en)
                                    if l2:
                                        meta_obj['meta']['logo'] = (tmdb.TMDB_BACK_URL + l2)
                            print(f"[META][IMG] {imdb_id} chosen poster_lang={p_lang} backdrop_lang={b_lang}")
                            if movie_details.get('release_date'):
                                meta_obj['meta']['released'] = to_iso_z(movie_details.get('release_date'))
                                meta_obj['meta']['firstAired'] = to_iso_z(movie_details.get('release_date'))
                            print(f"[META][TMDB-OFFICIAL] Anime movie {imdb_id}: poster={'ok' if meta_obj['meta'].get('poster') else 'no'} background={'ok' if meta_obj['meta'].get('background') else 'no'}")
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
                        # Ensure upcoming flags present for anime series too
                        if type == 'series':
                            u = _mark_upcoming(meta['meta'].get('videos', []))
                            if u > 0:
                                meta['meta'].setdefault('behaviorHints', {})
                                meta['meta']['behaviorHints']['hasScheduledVideos'] = True
                                print(f"[META][UPCOMING][FLAG] hasScheduledVideos=True for {id} (anime)")
                    # Fallback immagini per anime: se mancano poster/background/logo, prova da Cinemeta (IMDb)
                    try:
                        if imdb_id and (not meta['meta'].get('poster') or not meta['meta'].get('background') or not meta['meta'].get('logo')):
                            cm_resp = await client.get(f"{cinemeta_url}/meta/{type}/{imdb_id}.json")
                            if cm_resp.status_code == 200:
                                cm = cm_resp.json()
                                if not meta['meta'].get('poster') and (cm.get('meta') or {}).get('poster'):
                                    meta['meta']['poster'] = cm['meta']['poster']
                                    print(f"[META][IMG][FALLBACK] Poster from Cinemeta for {imdb_id}")
                                if not meta['meta'].get('background') and (cm.get('meta') or {}).get('background'):
                                    meta['meta']['background'] = cm['meta']['background']
                                    print(f"[META][IMG][FALLBACK] Background from Cinemeta for {imdb_id}")
                                if not meta['meta'].get('logo') and (cm.get('meta') or {}).get('logo'):
                                    meta['meta']['logo'] = cm['meta']['logo']
                                    print(f"[META][IMG][FALLBACK] Logo from Cinemeta for {imdb_id}")
                    except Exception:
                        pass
                else:
                    # Get meta from kitsu addon if conversion failed
                    response = await client.get(f"{kitsu.kitsu_addon_url}/meta/{type}/{id.replace(':','%3A')}.json")
                    meta = response.json()

            # Not compatible id -> redirect to original addon
            else:
                return RedirectResponse(f"{addon_url}/meta/{type}/{id}.json")


            meta['meta']['id'] = id

            # Fallback immagini generale: se mancano poster/background/logo, prova da Cinemeta
            try:
                imdb_for_cm = meta['meta'].get('imdb_id') or (id if id.startswith('tt') else None)
                need_poster = not meta['meta'].get('poster')
                need_bg = not meta['meta'].get('background')
                need_logo = not meta['meta'].get('logo')
                if imdb_for_cm and (need_poster or need_bg or need_logo):
                    cm_source = None
                    if 'cinemeta_meta' in locals() and cinemeta_meta:
                        cm_source = cinemeta_meta
                    else:
                        r = await client.get(f"{cinemeta_url}/meta/{type}/{imdb_for_cm}.json")
                        if r.status_code == 200:
                            cm_source = r.json()
                    if cm_source and cm_source.get('meta'):
                        if need_poster and cm_source['meta'].get('poster'):
                            meta['meta']['poster'] = cm_source['meta']['poster']
                            print(f"[META][IMG][FALLBACK] Poster from Cinemeta for {imdb_for_cm}")
                        if need_bg and cm_source['meta'].get('background'):
                            meta['meta']['background'] = cm_source['meta']['background']
                            print(f"[META][IMG][FALLBACK] Background from Cinemeta for {imdb_for_cm}")
                        if need_logo and cm_source['meta'].get('logo'):
                            meta['meta']['logo'] = cm_source['meta']['logo']
                            print(f"[META][IMG][FALLBACK] Logo from Cinemeta for {imdb_for_cm}")
            except Exception:
                pass

            # Poster: mantieni quello TMDB nel dettaglio; usa Toast Ratings solo come fallback se mancante
            try:
                if settings.get('sp', '0') == '0':
                    imdb_val = meta['meta'].get('imdb_id') or (id if id.startswith('tt') else None)
                    if imdb_val and settings.get('tr', '0') == '1':
                        if not meta['meta'].get('poster'):
                            meta['meta']['poster'] = f"{translator.RATINGS_SERVER}/{type}/get_poster/{imdb_val}.jpg"
                            print(f"[META][IMG][TOAST] Poster from Toast Ratings for {imdb_val}")
            except Exception:
                pass
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

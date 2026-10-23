from api import tmdb
from api import tvdb
from api import fanart
from anime import kitsu
import httpx
import asyncio
import urllib.parse
import translator
import math
import json

REQUEST_TIMEOUT = 100
MAX_CAST_SEARCH = 3
TMDB_ERROR_EPISODE_OFFSET = 50
MAX_TRANSLATE_EPISODES = 20

# Load TMDB exceptions
with open("anime/tmdb_exceptions.json", "r", encoding="utf-8") as f:
    TMDB_EXCEPTIONS = json.load(f) 

async def build_metadata(imdb_id: str, type: str, language: str, tmdb_key: str):
    tmdb_id = None
    if 'tt' in imdb_id:
        tmdb_id = await tmdb.convert_imdb_to_tmdb(imdb_id, language, tmdb_key, type)
    if 'tmdb:' in imdb_id: 
        tmdb_id = imdb_id.replace('tmdb:', '')
    elif tmdb_id != None and 'tmdb:' in tmdb_id:
        tmdb_id = tmdb_id.replace('tmdb:', '')
    elif 'error' in tmdb_id:
        return { 
            "meta": {
                "id": "error:tmdb-key",
                "name": "Invalid TMDB Key",
                "description": "Invalid TMDB Key",
                "poster": "https://i.imgur.com/Zi5UZV3.png",
                "type": type
            }
        }, {}

    async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT) as client:

        if type == 'movie':
            parse_title = 'title'
            default_video_id = imdb_id
            has_scheduled_videos = False
            tasks = [
                tmdb.get_movie_details(client, tmdb_id, language, tmdb_key),
                fanart.get_fanart_movie(client, tmdb_id)
            ]

        elif type == 'series':
            parse_title = 'name'
            default_video_id = None
            has_scheduled_videos = True
            tasks = [
                tmdb.get_series_details(client, tmdb_id, language, tmdb_key),
                fanart.get_fanart_series(client, tmdb_id)
            ]
        
        tasks.append(client.get(f"https://v3-cinemeta.strem.io/meta/{type}/{imdb_id}.json"))
        data = await asyncio.gather(*tasks)
        tmdb_data, fanart_data = data[0], data[1]
        if data[2].status_code == 200:
            cinemeta_data = data[2].json()
        else:
            cinemeta_data = {'meta': {}}
        
        # Empty tmdb data
        if len(tmdb_data) == 0:
            return {"meta": {}}, cinemeta_data

        # Invalid TMDB key error
        if tmdb_data.get('error'):
            return { 
                    "meta": {
                        "id": "error:tmdb-key",
                        "name": "Invalid TMDB Key",
                        "description": "Invalid TMDB Key",
                        "poster": "https://i.imgur.com/Zi5UZV3.png",
                        "type": type
                    }
            }, {}
        
        title = tmdb_data.get(parse_title, '')
        poster_path = tmdb_data.get('poster_path', '')
        backdrop_path = tmdb_data.get('backdrop_path', '')
        slug = f"{type}/{title.lower().replace(' ', '-')}-{tmdb_data.get('imdb_id', '').replace('tt', '')}"
        logo = extract_logo(fanart_data, tmdb_data, cinemeta_data, language)
        directors, writers= extract_crew(tmdb_data)
        cast = extract_cast(tmdb_data)
        genres = extract_genres(tmdb_data)
        year = extract_year(tmdb_data, type)
        trailers = extract_trailers(tmdb_data)
        rating = cinemeta_data.get('meta', {}).get('imdbRating', '')

        meta = {
            "meta": {
                "imdb_id": tmdb_data.get('imdb_id',''),
                "name": title,
                "type": type,
                "cast": cast,
                "country": (tmdb_data.get('origin_country') or [''])[0],
                "description": tmdb_data.get('overview', ''),
                "director": directors,
                "genre": genres,
                "imdbRating": rating,
                "released": tmdb_data.get('release_date', 'TBA')+'T00:00:00.000Z' if type == 'movie' else tmdb_data.get('first_air_date', 'TBA')+'T00:00:00.000Z',
                "slug": slug,
                "writer": writers,
                "year": year,
                "poster": tmdb.TMDB_POSTER_URL + poster_path if poster_path else None,
                "background": tmdb.TMDB_BACK_URL + backdrop_path if backdrop_path else None,
                "logo": logo,
                "runtime": convert_minutes_hours(tmdb_data.get('runtime','')) if type == 'movie' else convert_minutes_hours(extract_series_episode_runtime(tmdb_data, cinemeta_data)),
                "id": 'tmdb:' + str(tmdb_data.get('id', '')),
                "genres": genres,
                "releaseInfo": year,
                "trailerStreams": trailers,
                "links": build_links(imdb_id, title, slug, rating, cast, writers, directors, genres),
                "behaviorHints": {
                    "defaultVideoId": default_video_id,
                    "hasScheduledVideos": has_scheduled_videos
                }
            }
        }

        if type == 'series':
            meta['meta']['videos'] = await series_build_episodes(client, imdb_id, tmdb_id, tmdb_data.get('seasons', []), tmdb_data['external_ids']['tvdb_id'], tmdb_data['number_of_episodes'], language, tmdb_key)

        return meta, cinemeta_data


async def series_build_episodes(client: httpx.AsyncClient, imdb_id: str, tmdb_id: str, seasons: list, tvdb_series_id: int, tmdb_episodes_count: int, language: str, tmdb_key: str) -> list:
    tasks = []
    videos = []

    # Fetch TMDB request for seasons details
    for season in seasons:
        tasks.append(tmdb.get_season_details(client, tmdb_id, season['season_number'], language, tmdb_key))

    tmdb_seasons = await asyncio.gather(*tasks)

    # Anime tvdb mapping
    if ('kitsu' in imdb_id or 'mal' in imdb_id or imdb_id in kitsu.imdb_ids_map) and imdb_id not in TMDB_EXCEPTIONS:
        # Use TVDB data

        # Extract pre translated episodes
        episodes_tasks = []
        abs_episode_count = tmdb_episodes_count + TMDB_ERROR_EPISODE_OFFSET
        total_pages = math.ceil(abs_episode_count / tvdb.EPISODE_PAGE)
        for i in range(max(1, total_pages)):
            episodes_tasks.append(tvdb.get_translated_episodes(client, tvdb_series_id, i, language))
        
        translated_episodes = []
        episodes_tasks_result = await asyncio.gather(*episodes_tasks)
        for result in episodes_tasks_result:
            translated_episodes.extend(result['data']['episodes'])
        
        # Build episodes meta
        not_fully_translated_counter = 0
        for episode in translated_episodes:
            if episode['seasonNumber'] != 0: # Not for specials
                video = {
                    "name": f"{translator.EPISODE_TRANSLATIONS[language]} {episode['number']}" if episode['name'] == None else episode['name'],
                    "season": episode['seasonNumber'],
                    "number": episode['number'],
                    "firstAired": episode['aired'] + 'T05:00:00.000Z' if episode['aired'] is not None else None,
                    "rating": "0",
                    "overview": '' if episode['overview'] == None else episode['overview'],
                    "thumbnail": tvdb.IMAGE_URL + episode['image'] if episode['image'] != None else None,
                    "id": f"{imdb_id}:{episode['seasonNumber']}:{episode['number']}",
                    "released": episode['aired'] + 'T05:00:00.000Z' if episode['aired'] is not None else None,
                    "episode": episode['number'],
                    "description": ''
                }

                # Insert not fully translated episode to try translate it with TMDB
                if episode['seasonNumber'] != 0 and (episode['name'] == None or episode['overview'] == None) and not_fully_translated_counter < MAX_TRANSLATE_EPISODES:
                    video['tvdb_id'] = episode['id']
                    not_fully_translated_counter += 1
                
                videos.append(video)

        return await translator.translate_episodes(client, videos, language, tmdb_key)


    # TMDB episodes builder
    for season in tmdb_seasons:
        for episode_number, episode in enumerate(season['episodes'], start=1):
            videos.append(
                {
                    "name": episode['name'],
                    "season": episode['season_number'],
                    "number": episode_number,
                    "firstAired": episode['air_date'] + 'T05:00:00.000Z' if episode['air_date'] is not None else None,
                    "rating": str(episode['vote_average']),
                    "overview": episode['overview'],
                    "thumbnail": tmdb.TMDB_BACK_URL + episode['still_path'] if episode.get('still_path', '') is not None else None,
                    "id": f"{imdb_id}:{episode['season_number']}:{episode_number}",
                    "released": episode['air_date'] + 'T05:00:00.000Z' if episode['air_date'] is not None else None,
                    "episode": episode_number,
                    "description": episode['overview']
                }
            )

    return videos


def convert_minutes_hours(value):
    # Unknown runtime (e.g. upcoming series: 'N/A min', movie runtime None)
    try:
        total_minutes = int(str(value).replace("min", "").strip())
    except ValueError:
        return ''
    
    if total_minutes < 60:
        return f"{total_minutes}min"
    
    hours = total_minutes // 60
    minutes = total_minutes % 60
    
    if minutes > 0:
        return f"{hours}h{minutes}min"
    else:
        return f"{hours}h"
    

def extract_series_episode_runtime(tmdb_data: dict, cinemeta_data: dict) -> str:
    runtime = 0
    if len(tmdb_data.get('episode_run_time', [])) > 0:
        runtime = tmdb_data['episode_run_time'][0]
    else:
        runtime = (tmdb_data.get('last_episode_to_air') or {}).get('runtime', 'N/A')

    # Cinemeta fallback
    if not runtime:
        return cinemeta_data.get('meta', {}).get('runtime', 0)

    return str(runtime) + ' min'


def extract_logo(fanart_data: dict, tmdb_data: dict, cinemeta_data: dict, language: str) -> str:
    lang_iso_639_1 = language.split('-')[0]
    # Try TMDB logo
    if len(tmdb_data.get('images', {}).get('logos', [])) > 0:
        for logo in tmdb_data['images']['logos']:
            if logo['iso_639_1'] == lang_iso_639_1:
                return tmdb.TMDB_POSTER_URL + logo['file_path']

    # FanArt
    en_logo = ''
    # Try HD logo
    for logo in fanart_data.get('hdmovielogo', []):
        if logo['lang'] == 'en':
            en_logo = logo['url']
        elif logo['lang'] == lang_iso_639_1:
            return logo['url']
    
    # Try normal logo
    for logo in fanart_data.get('movielogo', []):
        if logo['lang'] == 'en':
            en_logo = logo['url']
        elif logo['lang'] == lang_iso_639_1:
            return logo['url']
        
    # Cinemeta
    if not en_logo:
        return cinemeta_data.get('meta', {}).get('logo')
    else:
        return en_logo


def extract_cast(tmdb_data: dict):
    cast = []
    for person in tmdb_data['credits']['cast'][:MAX_CAST_SEARCH]:
        if person['known_for_department'] == 'Acting':
            cast.append(person['name'])

    return cast


def extract_crew(tmdb_data: dict):
    directors = []
    writers = []
    for person in tmdb_data['credits']['crew']:
        if person['department'] == 'Writing' and person['name'] not in writers:
                writers.append(person['name'])
        elif person['known_for_department'] == 'Directing' and person.get('job', '') == 'Director' and person['name'] not in directors:
            directors.append(person['name'])
        
    return directors, writers


def extract_genres(tmdb_data: dict) -> list:
    genres = []
    for genre in tmdb_data['genres']:
        genres.append(genre['name'])

    return genres


def extract_year(tmdb_data: dict, type: str):
    if type == 'movie':
        try:
            return tmdb_data['release_date'].split('-')[0]
        except:
            return ''
    elif type == 'series':
        try:
            first_air = tmdb_data['first_air_date'].split('-')[0]
            last_air = ''
            if tmdb_data['status'] == 'Ended':
                last_air = tmdb_data['last_air_date'].split('-')[0]
            return f"{first_air}-{last_air}"
        except:
            return ''
    

def extract_trailers(tmdb_data):
    videos = tmdb_data.get('videos', { "results": [] })
    trailers = []
    for video in videos['results']:
        if video['type'] == 'Trailer' and video['site'] == 'YouTube':
            trailers.append({
                "title": video['name'],
                "ytId": video['key']
            })
    return trailers

def build_links(imdb_id: str, title: str, slug: str, rating: str, 
                cast: list, writers: list, directors: str, genres: list) -> list:
    links = [
        {
            "name": rating,
            "category": "imdb",
            "url": f"https://imdb.com/title/{imdb_id}"
        },
        {
            "name": title,
            "category": "share",
            "url": f"https://www.strem.io/s/movie/{slug}"
        },
    ]

    # Genres
    for genre in genres:
        links.append({
            "name": genre,
            "category": "Genres",
            "url": f"stremio:///discover/https%3A%2F%2FPLACEHOLDER%2Fmanifest.json/movie/top?genre={urllib.parse.quote(genre)}"
        })

    # Cast
    for actor in cast:
        links.append({
            "name": actor,
            "category": "Cast",
            "url": f"stremio:///search?search={urllib.parse.quote(actor)}"
        })

    # Writers
    for writer in writers:
        links.append({
            "name": writer,
            "category": "Writers",
            "url": f"stremio:///search?search={urllib.parse.quote(writer)}"
        })

    # Director
    for director in directors:
        links.append({
            "name": director,
            "category": "Directors",
            "url": f"stremio:///search?search={urllib.parse.quote(director)}"
        })

    return links
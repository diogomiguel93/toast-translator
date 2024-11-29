from diskcache import Cache
import tmdb
import asyncio
import httpx

# Cache set
translations_cache = Cache('/tmp/translation_strings')
translations_cache.clear()


async def translate_with_api(client: httpx.AsyncClient, text: str, source='en', target='it') -> str:

    translation = translations_cache.get(text)
    if translation == None:
        api_url = f"https://lingva-translate-azure.vercel.app/api/v1/{source}/{target}/{text}"

        response = await client.get(api_url)
        translated_text = response.json().get('translation', '')
        translations_cache.set(text, translated_text)
    else:
        translated_text = translation

    return translated_text


def translate_catalog(original: dict, tmdb_meta: dict, skip_poster, toast_ratings) -> dict:
    new_catalog = original

    for i, item in enumerate(new_catalog['metas']):
        try:
            type = item['type']
            type_key = 'movie' if type == 'movie' else 'tv'
            detail = tmdb_meta[i][f"{type_key}_results"][0]
        except:
            # Set poster if contend not have tmdb informations
            if toast_ratings == '1':
                if 'tt' in tmdb_meta[i]['imdb_id']:
                    item['poster'] = f"https://toastflix-ratings.hf.space/get_poster/{tmdb_meta[i]['imdb_id']}.jpg"

        else:
            try: item['name'] = detail['title'] if type == 'movie' else detail['name']
            except: pass

            try: item['description'] = detail['overview']
            except: pass

            try: item['background'] = tmdb.TMDB_BACK_URL + detail['backdrop_path']
            except: pass

            if skip_poster == '0':
                try: 
                    if toast_ratings == '1':
                        item['poster'] = f"https://toastflix-ratings.hf.space/get_poster/{tmdb_meta[i]['imdb_id']}.jpg?tmdb_path={detail['poster_path']}&tmdb_rate={detail['vote_average']}"
                    else:
                        item['poster'] = tmdb.TMDB_POSTER_URL + detail['poster_path']
                except Exception as e: 
                    print(e)


    return new_catalog


async def translate_episodes(client: httpx.AsyncClient, original_episodes: list[dict]):
    translate_index = []
    tasks = []
    new_episodes = original_episodes

    # Select not translated episodes
    for i, episode in enumerate(original_episodes):
        if 'tvdb_id' in episode:
            tasks.append(tmdb.get_tmdb_data(client, episode['tvdb_id'], "tvdb_id"))
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
            try: new_episodes[t_index]['thumbnail'] = tmdb.TMDB_BACK_URL + detail['still_path']
            except: pass

    return new_episodes
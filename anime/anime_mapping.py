import httpx
import bisect
import json
import asyncio

# Map for IDs
anime_mapping_url = 'https://raw.githubusercontent.com/Fribb/anime-lists/refs/heads/master/anime-list-full.json'
anime_id_map = None

# Map for season/episode
anime_db_map_url = 'https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json'
anime_season_map = None

# Load anidb Extension from file
with open("anime/anidb_extension.json", "r", encoding="utf-8") as f:
    anidb_extension = json.load(f) 

# Load anime mapping Extension from file
with open("anime/anime_mapping_extension.json", "r", encoding="utf-8") as f:
    anime_mapping_extension = json.load(f) 

async def download_maps():
    global anime_id_map, anime_season_map
    async with httpx.AsyncClient(timeout=20) as client:
        tasks = [
            client.get(anime_mapping_url),
            client.get(anime_db_map_url)
        ]
        results = await asyncio.gather(*tasks)
        anime_id_map = results[0].json() + anime_mapping_extension
        anime_season_map = {**results[1].json(), **anidb_extension}
        

def load_kitsu_map() -> dict:
    """
    Mappa per convertire un id kitsu in un id imdb
    """
    raw_map = anime_id_map#httpx.Client().get(anime_mapping_url).json() + anime_mapping_extension
    mapping_list = {}

    for item in raw_map:
        kitsu_id = item.get('kitsu_id', None)
        imdb_id = item.get('imdb_id', None)
        if kitsu_id != None and imdb_id != None and 'tt' in imdb_id:
            mapping_list[kitsu_id] = imdb_id

    return mapping_list


def load_mal_map() -> dict:
    """
    Mappa per convertire un id mal in un id imdb
    """
    raw_map = anime_id_map#httpx.Client().get(anime_mapping_url).json() + anime_mapping_extension
    mapping_list = {}

    for item in raw_map:
        mal_id = item.get('mal_id', None)
        imdb_id = item.get('imdb_id', None)
        if mal_id != None and imdb_id != None and 'tt' in imdb_id:
            mapping_list[mal_id] = imdb_id

    return mapping_list


def load_imdb_map() -> dict:
    """
    Genera una mappa con chiavi imdb_id con al suo interno:
    Tutti gli id corrispondenti di kitsu, animeDB e MAL.
    Serve per incorporare tutti le stagioni in un unico id (imdb)
    """

    raw_map = anime_id_map#httpx.Client().get(anime_mapping_url).json() + anime_mapping_extension
    anidb_map = anime_season_map
    #with httpx.Client() as client:
        #anidb_map = {**client.get(anime_db_map_url).json(), **anidb_extension}
    map = {}

    for item in raw_map:

        value = item.get("imdb_id")
        imdb_id: str | None = value[0] if isinstance(value, list) and value else value

        if imdb_id != None:
            kitsu_id: str | None = item.get('kitsu_id')
            anidb_id: str | None  = item.get('anidb_id')
            mal_id: str | None  = item.get('mal_id')

            # Create
            if imdb_id not in map:
                map[imdb_id] = {
                    "kitsu_ids": [],
                    "anidb_ids": [],
                    "mal_ids": []
                }
                
            # Update
            if kitsu_id != None and anidb_id != None:
                kitsu_id, anidb_id = str(kitsu_id), str(anidb_id)
                map[imdb_id]['anidb_ids'].append(anidb_id)
                insert_sorted_kitsu_insort(map[imdb_id]['kitsu_ids'], kitsu_id, anidb_map[anidb_id].get('tvdb_season'), anidb_map[anidb_id].get('tvdb_epoffset'))
            if mal_id != None:
                mal_id = str(mal_id)
                map[imdb_id]['mal_ids'].append(mal_id)

    return map


def load_kitsu_to_anidb_map():
    """
    Genera una mappa da chiave valore kitsu id -> anime db id
    """
    raw_map = anime_id_map#httpx.Client().get(anime_mapping_url).json()
    map = {}

    for item in raw_map:
        kitsu_id = item.get('kitsu_id', None)
        anidb_id = item.get('anidb_id', None)

        if kitsu_id != None and anidb_id != None:
            map[kitsu_id] = anidb_id
    
    return map

def insert_sorted_kitsu_insort(kitsu_list, kitsu_id, season, epoffset):
    """
    Inserisce un nuovo kitsu_id nella lista kitsu_list già ordinata
    per season e poi per epoffset, usando bisect.insort.
    """
    new_entry = {kitsu_id: {"season": season, "epoffset": epoffset}}
    # crea una chiave per ordinamento
    new_key = (season or 0, epoffset or 0)

    # crea una lista ausiliaria di chiavi
    keys = [(list(entry.values())[0].get("season") or 0,
             list(entry.values())[0].get("epoffset") or 0)
            for entry in kitsu_list]

    # trova la posizione di inserimento
    idx = bisect.bisect_left(keys, new_key)

    # inserisci il dict nella posizione corretta
    kitsu_list.insert(idx, new_entry)


def load_anidb_map():
    map = httpx.Client().get(anime_db_map_url).json()
    return map

if __name__ == '__main__':
    imdb_map = load_imdb_map()
    anidb_map = load_anidb_map()
    print(len(imdb_map))

    titans = imdb_map['tt2560140']
    #print(anidb_map["9541"])
    print(titans)
    """
    kitsu_anidb_map = load_kitsu_to_anidb_map()
    anidb_map = load_anidb_map()

    kitsu_id = 41982
    print(kitsu_anidb_map[kitsu_id])
    item = anidb_map[str(kitsu_anidb_map[kitsu_id])]
    print(item)
    """

"""
{'livechart_id': 754, 'thetvdb_id': 267440, 'anime-planet_id': 'attack-on-titan-2nd-season', 'imdb_id': 'tt2560140', 'anisearch_id': 10082, 'themoviedb_id': 1429, 'anidb_id': 10944, 'kitsu_id': 8671, 'mal_id': 25777, 'type': 'TV', 'notify.moe_id': 'jh2JpFimg', 'anilist_id': 20958}   
{'livechart_id': 2737, 'thetvdb_id': 267440, 'anime-planet_id': 'attack-on-titan-3rd-season', 'imdb_id': 'tt2560140', 'anisearch_id': 12550, 'themoviedb_id': 1429, 'anidb_id': 13241, 'kitsu_id': 13569, 'mal_id': 35760, 'type': 'TV', 'notify.moe_id': '_7O42FiiR', 'anilist_id': 99147} 
{'livechart_id': 3558, 'thetvdb_id': 267440, 'anime-planet_id': 'attack-on-titan-3rd-season-part-ii', 'imdb_id': 'tt2560140', 'anisearch_id': 13942, 'themoviedb_id': 1429, 'anidb_id': 14444, 'kitsu_id': 41982, 'mal_id': 38524, 'type': 'TV', 'notify.moe_id': 'k8I25a-ig', 'anilist_id': 104578}
{'livechart_id': 9491, 'thetvdb_id': 267440, 'anime-planet_id': 'attack-on-titan-the-final-season', 'imdb_id': 'tt2560140', 'anisearch_id': 14484, 'themoviedb_id': 1429, 'anidb_id': 14977, 'kitsu_id': 42422, 'mal_id': 40028, 'type': 'TV', 'notify.moe_id': 'drmaMJIZg', 'anilist_id': 110277}
{'livechart_id': 11174, 'thetvdb_id': 267440, 'anime-planet_id': 'attack-on-titan-the-final-season-the-final-chapters', 'imdb_id': 'tt2560140', 'anisearch_id': 17265, 'themoviedb_id': 1429, 'anidb_id': 17303, 'kitsu_id': 46038, 'mal_id': 51535, 'type': 'SPECIAL', 'notify.moe_id': 'ii
{'livechart_id': 10487, 'thetvdb_id': 267440, 'anime-planet_id': 'attack-on-titan-the-final-season-part-ii', 'imdb_id': 'tt2560140', 'anisearch_id': 16103, 'themoviedb_id': 1429, 'anidb_id': 16177, 'kitsu_id': 44240, 'mal_id': 48583, 'type': 'TV', 'notify.moe_id': 'g9nvf6wMR', 'anilist_id': 131681}
"""
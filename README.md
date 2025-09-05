# Toast Translator (Traduttore per Add-on Stremio)

Traduttore e arricchitore in italiano per gli add-on di Stremio. Mette in proxy qualsiasi add-on compatibile, traducendo titoli/descrizioni in italiano e arricchendo poster/sfondo/logo tramite TMDB e Cinemeta con fallback intelligenti. Include una piccola UI web per installare e generare link.

## Cosa fa

- Avvolge qualsiasi add-on Stremio compatibile ed espone gli stessi endpoint con contenuti localizzati in italiano.
- Aggiunge la bandierina 🇮🇹 e metadati nel manifest; opzionalmente impone idPrefixes/resources.
- Traduce:
  - Catalogo: titolo, overview; arricchisce poster/sfondo quando possibile.
  - Meta (film/serie/anime): nome, descrizione; mappa e arricchisce i video per gli anime; aggiunge i flag "in arrivo".
- Arricchimento immagini con fallback a più livelli:
  1) TMDB (preferenza italiano)
  2) TMDB (fallback inglese) – secondo tentativo solo se mancano immagini
  3) Fallback da Cinemeta (poster/sfondo/logo) tramite IMDb id
  4) Poster Toast Ratings opzionale quando attivato (solo meta)
- Logica specifica per anime:
  - Supporta ID: imdb (tt...), kitsu: e mal:
  - Converte kitsu/mal → IMDb tramite add-on Kitsu; rimappa gli id degli episodi usando un file di mapping mantenuto.
  - Se TMDB non ha il logo, recupera `meta.logo` da Cinemeta.
  - Mantiene l’etichetta “Prossimamente” per episodi con data futura.
- Mantiene inalterati e inoltra gli endpoint streams/subtitles.

## Endpoint in proxy

Base path: `/{base64-addon-url}/{user_settings}`

- Manifest: `/manifest.json`
- Catalog: `/catalog/{type}/{path}`
- Meta: `/meta/{type}/{id}.json`
- Stream: `/stream/{path}` (redirect)
- Sottotitoli: `/subtitles/{path}` (redirect)
- Pagine di supporto:
  - `/` UI di configurazione (login Stremio, selezione add-on, applica)
  - `/link_generator` generatore di link tradotti senza login

Tipi supportati: `movie`, `series`, `anime`.

## Dettagli di traduzione e arricchimento

- Catalogo
  - Interroga TMDB via endpoint ufficiale /find usando l’imdb_id o l’id di ogni item.
  - Sovrascrive quando possibile:
    - name: dal titolo/nome TMDB
    - description: dalla overview TMDB
    - background: backdrop TMDB se disponibile
    - poster:
      - Se `skipPoster=0` → poster TMDB (o Toast Ratings quando `tr=1`; al momento disattivato nella UI)
      - Se `skipPoster=1` → lascia il poster originale

- Meta (id IMDb tt...)
  - Usa prima l’API ufficiale TMDB (movie o tv), componendo:
    - name, description, poster, background, logo (preferendo immagini italiane)
    - per le serie: tutte le stagioni/episodi via /tv/{id}/season/{n}; marca gli episodi futuri come “Prossimamente” e imposta behaviorHints.hasScheduledVideos
  - Se uno tra poster/sfondo/logo manca, effettua un secondo tentativo su TMDB con include_image_language in inglese e riprova la selezione
  - Poi fa il merge sopra i metadati di Cinemeta (solo non-anime), preservando liste video più complete ed evitando sovrascritture con campi vuoti
  - Se ancora mancano immagini, usa i campi di Cinemeta: meta.poster/background/logo

- Meta (kitsu/mal)
  - Converte in IMDb usando l’add-on Kitsu; poi ripete il flusso TMDB ufficiale descritto sopra
  - Post-processing per anime:
    - Per le serie: rimappa gli id degli episodi allo schema Kitsu; preserva i flag “in arrivo”
    - Se manca il logo TMDB, recupera il logo da Cinemeta meta
    - Sicurezza finale: se mancano poster/sfondo/logo, recupera da Cinemeta via IMDb

## Ordine dei fallback (immagini)

Priorità per ogni asset (poster, sfondo, logo):
1. Immagini TMDB con preferenza italiano
2. Fallback TMDB in inglese (se ancora mancano)
3. Immagini da Cinemeta (meta)
4. Poster Toast Ratings (opzionale, solo meta, quando `tr=1`)

Loghi:
- Per anime e meta in generale, si usa `meta.logo` di Cinemeta quando TMDB non lo fornisce.

## Compatibilità e limitazioni

- Add-on compatibili (esempi): Cinemeta, Kitsu, Anime Catalogs, Trakt, cataloghi basati su IMDb, ecc. Vedi la allowlist in `static/addonCard.js`.
- Non tutti gli elementi di catalogo hanno un imdb id noto; il fallback del poster Toast Ratings (meta) può aiutare quando abilitato.
- L’arricchimento del catalogo non aggiunge loghi (i loghi sono gestiti nelle risposte meta).

## Impostazioni utente

Le impostazioni utente sono codificate nel segmento `user_settings` dell’URL come `sp=<0|1>,tr=<0|1>` dove:
- sp: salta il poster nel catalogo (0 default = usa poster TMDB/Toast, 1 = lascia l’originale)
- tr: poster Toast Ratings nel meta quando manca il poster (0 default = off). La UI forza `tr=0`.

Esempio URL completo del manifest:
- `https://<questo-host>/<base64(addon-url)>/sp=0,tr=0/manifest.json`

## Deploy ed esecuzione

Variabili d’ambiente:
- TMDB_API_KEY: obbligatoria per le chiamate all’API ufficiale TMDB
- PORT: usata da uvicorn/Procfile (fornita dalla piattaforma in PaaS)
- TR_SERVER: base URL opzionale per Toast Ratings (default incluso)

Installazione ed esecuzione locale:
- Python 3.10+
- `pip install -r requirements.txt`
- `uvicorn main:app --reload`

Vercel
- `vercel.json` instrada tutto verso `main.py`

Heroku o simili
- `Procfile` esegue `uvicorn main:app`

## Note di sviluppo

- Cache: TTL in-memory per lookup TMDB, traduzioni e risposte meta.
- Rate limiting: le chiamate TMDB fanno retry su HTTP 429 con backoff.
- Base immagini: https://image.tmdb.org/t/p/original (poster, backdrop, logo).
- Base Cinemeta: https://v3-cinemeta.strem.io
- Add-on Kitsu: https://anime-kitsu.strem.fun

## Licenza

Questo progetto traduce e fa da proxy ai metadati per uso personale. Rispetta i termini d’uso di TMDB e Stremio. Non viene ospitato alcun contenuto protetto da copyright.

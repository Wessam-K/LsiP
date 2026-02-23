# LsiP

Location Search Intelligence Platform — a tool I built to pull business data from Google Maps, enrich it (emails, websites, social links), and score locations for market analysis.

It started as a way to automate the tedious process of manually searching Google Maps for business info, and grew into a full pipeline with grid search, competitor heatmaps, and a dashboard.

## What it does

- **Search Google Maps** using the Places API (New) and get way more than the default 60 results by splitting the area into a grid of overlapping sub-regions
- **Enrich results** by crawling business websites for emails and detecting social media pages (Facebook, Instagram, etc.)
- **Classify businesses** as brand/chain vs local independent
- **Generate heatmaps** showing where competitors are concentrated
- **Score locations** based on demand, competition, and accessibility
- **Export to CSV** with proper Arabic/Unicode support
- **Live progress** via SSE so you can see results coming in as the grid search runs

## Requirements

- Python 3.11+
- PostgreSQL 14+
- A Google Cloud API key with Places API (New) enabled

## Setup

```bash
git clone https://github.com/Wessam-K/LsiP.git
cd LsiP

python -m venv .venv
.venv\Scripts\activate          # on Windows
# source .venv/bin/activate     # on macOS/Linux

pip install -r requirements.txt
```

Copy the env template and fill in your API key:

```bash
cp .env.example .env
```

Then edit `.env`:

```
GOOGLE_PLACES_API_KEY=your_key_here
```

Set up the database and run:

```bash
createdb places_db
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Go to http://localhost:8000

## Getting a Google API key

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Go to APIs & Services > Library, search for **Places API (New)** and enable it
4. Go to APIs & Services > Credentials, click Create Credentials > API key
5. Copy the key into your `.env` file

You should restrict the key to only the Places API and set IP restrictions if you're deploying this anywhere. Google gives $200/month free credit for Maps Platform.

Some rough pricing:
- Text Search: ~$32 per 1,000 requests
- Place Details: ~$17-20 per 1,000 requests

The app uses field masks to only request the fields it needs, which helps keep costs down.

## Docker alternative

```bash
cp .env.example .env
# edit .env with your API key
docker-compose up --build
```

## API endpoints

Base path is `/api/v1`

- `POST /search` — search for places
- `POST /search/stream` — same but with SSE progress updates
- `GET /places` — list stored places
- `GET /places/{id}` — get place details with enrichment data
- `POST /heatmap` — compute competitor density
- `POST /score` — score places by location quality
- `GET /top-locations` — highest scored locations
- `GET /density` — competitor density at a specific point
- `GET /export/csv` — download everything as CSV

There's also `/docs` for the Swagger UI and `/health` for health checks.

### Search example

```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "clothing stores", "location": "31.2001, 29.9187", "radius_km": 20}'
```

## How the grid search works

Google caps results at 60 per search. To get around that, the app divides your search area into a grid of smaller overlapping circles (up to 5x5 = 25 sub-regions), runs a search in each one, and deduplicates by place ID. A 20km search can pull 300+ results this way.

## Project structure

```
app/
  main.py              - FastAPI app
  config.py            - settings from env vars
  schemas.py           - request/response models
  api/routes.py        - all endpoints
  db/models.py         - SQLAlchemy models
  db/session.py        - async db engine
  services/
    places_client.py   - Google Places API client + grid search
    enrichment.py      - website crawling, email extraction
    classifier.py      - brand vs local classification
    heatmap.py         - density computation
    scoring.py         - location scoring
static/index.html     - dashboard frontend
tests/                 - pytest test suite
alembic/              - database migrations
```

## Config

All config is through environment variables (see `.env.example`):

- `GOOGLE_PLACES_API_KEY` — required
- `DATABASE_URL` — async PostgreSQL connection string
- `MAX_REQUESTS_PER_SECOND` — rate limit for Google API (default: 5)
- `ENRICHMENT_TIMEOUT` — website crawl timeout in seconds (default: 10)
- `RESPECT_ROBOTS_TXT` — whether to honor robots.txt (default: true)

## Running tests

```bash
python -m pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).

Note: using the Google Places API is subject to [Google's Terms of Service](https://developers.google.com/maps/terms-20180207). You're responsible for your own API usage and billing.

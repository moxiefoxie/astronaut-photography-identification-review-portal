# Astronaut Photography Identification Review Portal

A collaborative review workspace for NASA astronaut photography. It is designed for Vercel and Supabase and deliberately leaves the source imagery on NASA's servers, so deploying the reviewer does not mean uploading the multi-gigabyte image archive.

## What is included

- Passwordless team sign-in with Supabase Auth
- Shared Postgres records with row-level security
- A paginated AI Catalog with model ranking scores, evidence, and live predictions
- Filtered JSON catalog exports containing NASA full-resolution and thumbnail links
- Grid and one-photo-at-a-time review modes
- Phone-friendly swipe right to accept and swipe left to reject
- Keyboard shortcuts: right/left accept or reject, up uncertain, down skip
- Searchable tags that can be removed, selected, or created without a browser prompt
- Per-reviewer decisions plus live team vote counts
- Live run progress and new candidates through Supabase Realtime
- 150-item pagination for large datasets
- A protected ingest API and a retrying incremental CSV publisher

## Architecture

The browser uses the Supabase publishable key. It can read team data and can only write reviews belonging to the signed-in user. The ingest endpoint runs on Vercel and uses the service-role key on the server. `INGEST_API_KEY` prevents an unauthenticated caller from feeding the endpoint.

NASA large and thumbnail URLs are stored as text. Images load directly from `eol.jsc.nasa.gov`; they are not copied into Supabase Storage or Vercel.

## 1. Create the Supabase project

1. Create a project at [Supabase](https://supabase.com/dashboard).
2. Open **SQL Editor**, paste [`supabase/schema.sql`](supabase/schema.sql), and run it once.
3. In **Project Settings → API**, copy:
   - Project URL
   - Publishable key
   - Service-role key
4. In **Authentication → URL Configuration**, initially add `http://localhost:3000`. Add the Vercel production URL after the first deployment.
5. Before sharing the app publicly, disable unrestricted new-user signups and invite the intended reviewers from Supabase Authentication. Otherwise, any address permitted to sign up would count as an authenticated team member.

Realtime is enabled for runs, candidates, predictions, and reviews by the schema. Supabase's Postgres Changes approach is intentionally used here because it is simple for a small review team. For a much larger audience, migrate these notifications to Supabase Broadcast.

## 2. Configure locally

```bash
cp environment.template .env.local
```

Fill in all four values in the ignored `.env.local` file. Generate the ingest secret with a password manager or a command such as `openssl rand -hex 32`. Never place real values in `environment.template`.

```bash
npm install
npm run dev
```

With no `.env.local`, the application starts in local demo mode. Demo decisions are stored in that browser only.

## 3. Deploy to Vercel

The NASA workspace currently has no Git remote, so either push it to GitHub and import it in Vercel with `ocean-review-app` as the Root Directory, or deploy this directory with the Vercel CLI.

Set these four environment variables for Production, Preview, and Development:

```text
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
SUPABASE_SERVICE_ROLE_KEY
INGEST_API_KEY
```

Never expose `SUPABASE_SERVICE_ROLE_KEY` or `INGEST_API_KEY` as a `NEXT_PUBLIC_` variable.

After the deployment succeeds, add the resulting `https://…vercel.app` URL to Supabase **Authentication → URL Configuration** as the Site URL and an allowed redirect URL.

## 4. Publish existing results

The publisher understands the columns already present in `production_round3_predictions.csv`: `image_id`, `score`, `categories`, `date`, coordinates, mission/roll/frame, and evidence.

```bash
export REVIEW_APP_URL=https://YOUR-APP.vercel.app
export INGEST_API_KEY=THE_SAME_SECRET_USED_BY_VERCEL

python3 scripts/ingest_results.py \
  ../production_round3_predictions.csv \
  --run-name "Ocean production round 3" \
  --expected-count 627
```

Rows are sent in idempotent batches of 100. The returned run UUID is printed after every batch.

## 5. Stream a live production run

Start the publisher as soon as the pipeline creates the CSV. It rereads the growing file and only publishes image IDs it has not seen during the process:

```bash
python3 scripts/ingest_results.py \
  ../next_production_predictions.csv \
  --run-name "Ocean production — full archive" \
  --expected-count 250000 \
  --watch
```

Each batch appears in the dashboard immediately. Reviewers do not wait for the full inference job. If the publisher is restarted, pass the run UUID it printed previously with `--run-id` so it continues the same run.

For a very large long-running pipeline, call `POST /api/ingest` directly at the end of each inference batch instead of using file watching. Request bodies accept up to 200 images and use this shape:

```json
{
  "run": {
    "id": "optional-existing-run-uuid",
    "name": "Full archive",
    "status": "running",
    "expected_count": 250000,
    "processed_count": 1200
  },
  "images": [
    {
      "id": "ISS073-E-604742",
      "image_url": "https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/ISS073/ISS073-E-604742.JPG",
      "thumbnail_url": "https://eol.jsc.nasa.gov/DatabaseImages/ESC/small/ISS073/ISS073-E-604742.JPG",
      "predictions": [
        { "tag": "confirmed_sunglint", "score": 1, "source": "geometry" }
      ]
    }
  ]
}
```

Send `Authorization: Bearer $INGEST_API_KEY`. Mark the run `complete` with a final request when inference ends.

## Data model

- `runs`: pipeline status and progress
- `images`: one canonical NASA image record
- `run_images`: an image's membership and ranking within a particular run
- `predictions`: model or metadata tags and scores
- `tags`: the shared controlled vocabulary plus team-created tags
- `reviews`: one decision per reviewer and run candidate
- `review_tags`: the reviewer-corrected tag set

This separation allows the same NASA photograph to appear in several experiments without duplicating its image metadata or mixing model outputs between runs.

## Validation

```bash
npm run typecheck
npm run lint
npm run build
python3 -m py_compile scripts/ingest_results.py
```

The app intentionally uses direct `<img>` URLs instead of Vercel Image Optimization so a large review run does not consume optimization quota for NASA-hosted thumbnails.

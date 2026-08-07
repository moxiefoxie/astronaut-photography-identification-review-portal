create extension if not exists pgcrypto;

create type public.run_status as enum ('queued', 'running', 'complete', 'failed');
create type public.review_decision as enum ('accept', 'reject', 'uncertain', 'skip');

create table public.runs (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  description text,
  status public.run_status not null default 'queued',
  expected_count integer,
  processed_count integer not null default 0 check (processed_count >= 0),
  inserted_count integer not null default 0 check (inserted_count >= 0),
  config jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create table public.images (
  id text primary key,
  image_url text not null,
  thumbnail_url text not null,
  captured_at timestamptz,
  latitude double precision,
  longitude double precision,
  mission text,
  roll text,
  frame text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.run_images (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.runs(id) on delete cascade,
  image_id text not null references public.images(id) on delete cascade,
  ranking_score double precision,
  created_at timestamptz not null default now(),
  unique (run_id, image_id)
);
create index run_images_run_created_idx on public.run_images(run_id, created_at);

create table public.tags (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9_]+$'),
  label text not null,
  description text,
  color text not null default '#38bdf8',
  active boolean not null default true,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now()
);

create table public.predictions (
  run_image_id uuid not null references public.run_images(id) on delete cascade,
  tag_id uuid not null references public.tags(id) on delete cascade,
  score double precision not null check (score >= 0 and score <= 1),
  source text not null,
  model_version text,
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (run_image_id, tag_id, source)
);

create table public.reviews (
  id uuid primary key default gen_random_uuid(),
  image_id uuid not null references public.run_images(id) on delete cascade,
  reviewer_id uuid not null references auth.users(id) on delete cascade,
  decision public.review_decision not null,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (image_id, reviewer_id)
);
create index reviews_reviewer_idx on public.reviews(reviewer_id, image_id);

create table public.review_tags (
  review_id uuid not null references public.reviews(id) on delete cascade,
  tag_id uuid not null references public.tags(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (review_id, tag_id)
);

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
create trigger runs_set_updated_at before update on public.runs for each row execute function public.set_updated_at();
create trigger reviews_set_updated_at before update on public.reviews for each row execute function public.set_updated_at();

alter table public.runs enable row level security;
alter table public.images enable row level security;
alter table public.run_images enable row level security;
alter table public.tags enable row level security;
alter table public.predictions enable row level security;
alter table public.reviews enable row level security;
alter table public.review_tags enable row level security;

create policy "team reads runs" on public.runs for select to authenticated using (true);
create policy "team reads images" on public.images for select to authenticated using (true);
create policy "team reads run images" on public.run_images for select to authenticated using (true);
create policy "team reads tags" on public.tags for select to authenticated using (true);
create policy "team creates tags" on public.tags for insert to authenticated with check (created_by = auth.uid());
create policy "team reads predictions" on public.predictions for select to authenticated using (true);
create policy "team reads reviews" on public.reviews for select to authenticated using (true);
create policy "reviewer creates own review" on public.reviews for insert to authenticated with check (reviewer_id = auth.uid());
create policy "reviewer updates own review" on public.reviews for update to authenticated using (reviewer_id = auth.uid()) with check (reviewer_id = auth.uid());
create policy "reviewer deletes own review" on public.reviews for delete to authenticated using (reviewer_id = auth.uid());
create policy "team reads review tags" on public.review_tags for select to authenticated using (true);
create policy "reviewer creates own review tags" on public.review_tags for insert to authenticated with check (
  exists (select 1 from public.reviews where reviews.id = review_id and reviews.reviewer_id = auth.uid())
);
create policy "reviewer deletes own review tags" on public.review_tags for delete to authenticated using (
  exists (select 1 from public.reviews where reviews.id = review_id and reviews.reviewer_id = auth.uid())
);

insert into public.tags (slug, label, description, color) values
  ('ocean_color', 'Ocean color', 'Unusual or scientifically relevant water color', '#38bdf8'),
  ('sediment_plume', 'Sediment plume', 'Sediment shapes, mixing and transport', '#f59e0b'),
  ('river_discharge', 'River discharge', 'River outflow entering a lake or ocean', '#fb7185'),
  ('algal_bloom_candidate', 'Algal bloom candidate', 'Possible algae or phytoplankton bloom; requires validation', '#22c55e'),
  ('sea_ice', 'Sea ice', 'Sea ice, floes, polynyas or pack ice', '#a5f3fc'),
  ('wave_patterns', 'Wave patterns', 'Visible wave refraction, diffraction or coherent bands', '#818cf8'),
  ('tidal_mixing_fronts', 'Tidal mixing / fronts', 'Tidal inlets, mixing boundaries and coastal fronts', '#2dd4bf'),
  ('shoreline_sediment_transport', 'Shoreline sediment transport', 'Longshore transport, erosion or accretion', '#f97316'),
  ('floating_material', 'Floating material', 'Possible non-biological floating material or flotsam', '#e879f9'),
  ('night_dynamic', 'Night imagery', 'Night lights, fishing fleets, lightning, sprites or bioluminescence', '#8b5cf6'),
  ('confirmed_sunglint', 'Confirmed sunglint', 'Geometry-supported sunglint confirmed by a reviewer', '#fde047')
on conflict (slug) do update set label = excluded.label, description = excluded.description, color = excluded.color;

do $$
begin
  alter publication supabase_realtime add table public.runs;
exception when duplicate_object then null;
end $$;
do $$
begin
  alter publication supabase_realtime add table public.run_images;
exception when duplicate_object then null;
end $$;
do $$
begin
  alter publication supabase_realtime add table public.predictions;
exception when duplicate_object then null;
end $$;
do $$
begin
  alter publication supabase_realtime add table public.reviews;
exception when duplicate_object then null;
end $$;

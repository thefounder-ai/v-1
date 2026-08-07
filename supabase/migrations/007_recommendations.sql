-- Grounded recommendation history. Run after 006_interest_profiles.sql.

create table if not exists public.recommendations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  summary text not null,
  next_step text not null,
  interest_snapshot jsonb not null default '[]'::jsonb,
  retrieval_query text not null,
  model text not null,
  trigger_event_count integer not null default 0,
  status text not null default 'active' check (status in ('active', 'expired', 'dismissed')),
  created_at timestamptz not null default now(),
  expires_at timestamptz
);

create table if not exists public.recommendation_items (
  id uuid primary key default gen_random_uuid(),
  recommendation_id uuid not null references public.recommendations(id) on delete cascade,
  product_id uuid not null references public.products(id) on delete restrict,
  rank integer not null check (rank between 1 and 10),
  reason text not null,
  retrieval_score numeric,
  created_at timestamptz not null default now(),
  unique (recommendation_id, product_id)
);

create index if not exists recommendations_user_created_idx
  on public.recommendations(user_id, created_at desc);

create index if not exists recommendations_active_idx
  on public.recommendations(user_id, status, created_at desc);

create index if not exists recommendation_items_recommendation_idx
  on public.recommendation_items(recommendation_id, rank);

alter table public.recommendations enable row level security;
alter table public.recommendation_items enable row level security;

drop policy if exists "Learners can read their own recommendations" on public.recommendations;
create policy "Learners can read their own recommendations"
  on public.recommendations for select
  using (auth.uid() = user_id);

drop policy if exists "Learners can create their own recommendations" on public.recommendations;
create policy "Learners can create their own recommendations"
  on public.recommendations for insert
  with check (auth.uid() = user_id);

drop policy if exists "Learners can update their own recommendations" on public.recommendations;
create policy "Learners can update their own recommendations"
  on public.recommendations for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Learners can read their own recommendation items" on public.recommendation_items;
create policy "Learners can read their own recommendation items"
  on public.recommendation_items for select
  using (
    exists (
      select 1 from public.recommendations
      where recommendations.id = recommendation_items.recommendation_id
        and recommendations.user_id = auth.uid()
    )
  );

drop policy if exists "Learners can create their own recommendation items" on public.recommendation_items;
create policy "Learners can create their own recommendation items"
  on public.recommendation_items for insert
  with check (
    exists (
      select 1 from public.recommendations
      where recommendations.id = recommendation_items.recommendation_id
        and recommendations.user_id = auth.uid()
    )
  );
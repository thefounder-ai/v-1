-- Behavioral event storage. Run after 004_vector_sync.sql.

create table if not exists public.activity_events (
  event_id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  event_type text not null check (
    event_type in (
      'page_view',
      'catalog_search',
      'filter_applied',
      'resource_view',
      'resource_click',
      'resource_dwell',
      'bookmark_added',
      'recommendation_opened',
      'recommendation_feedback',
      'learning_goal_updated'
    )
  ),
  resource_id uuid references public.products(id) on delete set null,
  search_query text check (search_query is null or char_length(search_query) <= 200),
  duration_seconds integer check (
    duration_seconds is null or duration_seconds between 0 and 86400
  ),
  metadata jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists activity_events_user_occurred_idx
  on public.activity_events(user_id, occurred_at desc);

create index if not exists activity_events_user_type_idx
  on public.activity_events(user_id, event_type);

create index if not exists activity_events_resource_idx
  on public.activity_events(resource_id);

alter table public.activity_events enable row level security;

drop policy if exists "Learners can read their own activity" on public.activity_events;
create policy "Learners can read their own activity"
  on public.activity_events for select
  using (auth.uid() = user_id);

drop policy if exists "Learners can add their own activity" on public.activity_events;
create policy "Learners can add their own activity"
  on public.activity_events for insert
  with check (auth.uid() = user_id);
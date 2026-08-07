-- Cached learner interest profiles. Run after 005_activity_events.sql.

create table if not exists public.user_interest_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  interest_snapshot jsonb not null default '[]'::jsonb,
  category_weights jsonb not null default '{}'::jsonb,
  skill_weights jsonb not null default '{}'::jsonb,
  search_terms jsonb not null default '[]'::jsonb,
  signal_summary text not null default '',
  event_count integer not null default 0,
  meaningful_event_count integer not null default 0,
  refresh_recommended boolean not null default false,
  profile_version integer not null default 1,
  last_event_at timestamptz,
  updated_at timestamptz not null default now()
);

create index if not exists user_interest_profiles_updated_idx
  on public.user_interest_profiles(updated_at desc);

alter table public.user_interest_profiles enable row level security;

drop policy if exists "Learners can read their own interest profile" on public.user_interest_profiles;
create policy "Learners can read their own interest profile"
  on public.user_interest_profiles for select
  using (auth.uid() = user_id);

drop policy if exists "Learners can insert their own interest profile" on public.user_interest_profiles;
create policy "Learners can insert their own interest profile"
  on public.user_interest_profiles for insert
  with check (auth.uid() = user_id);

drop policy if exists "Learners can update their own interest profile" on public.user_interest_profiles;
create policy "Learners can update their own interest profile"
  on public.user_interest_profiles for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

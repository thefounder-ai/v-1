-- Apply this migration in the Supabase SQL editor before using onboarding.
-- Supabase Auth owns auth.users; this table stores SkillOrbit-specific data.

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  role text not null default 'learner' check (role in ('learner', 'admin')),
  career_goal text,
  current_level text,
  weekly_minutes integer check (weekly_minutes is null or weekly_minutes between 30 and 10080),
  onboarding_complete boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "Learners can read their own profile" on public.profiles;
create policy "Learners can read their own profile"
  on public.profiles for select
  using (auth.uid() = user_id);

drop policy if exists "Learners can create their own profile" on public.profiles;
create policy "Learners can create their own profile"
  on public.profiles for insert
  with check (auth.uid() = user_id);

drop policy if exists "Learners can update their own profile" on public.profiles;
create policy "Learners can update their own profile"
  on public.profiles for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create index if not exists profiles_role_idx on public.profiles(role);
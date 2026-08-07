-- Learner progress tracking. Run after 011_auth_profile_trigger.sql.

create table if not exists public.user_progress (
  user_id uuid not null references auth.users(id) on delete cascade,
  product_id uuid not null references public.products(id) on delete cascade,
  status text not null check (status in ('started', 'completed')),
  updated_at timestamptz not null default now(),
  primary key (user_id, product_id)
);

create index if not exists user_progress_user_idx on public.user_progress(user_id);

alter table public.user_progress enable row level security;

drop policy if exists "Learners read own progress" on public.user_progress;
create policy "Learners read own progress"
  on public.user_progress for select
  using (auth.uid() = user_id);

drop policy if exists "Learners upsert own progress" on public.user_progress;
create policy "Learners upsert own progress"
  on public.user_progress for insert
  with check (auth.uid() = user_id);

drop policy if exists "Learners update own progress" on public.user_progress;
create policy "Learners update own progress"
  on public.user_progress for update
  using (auth.uid() = user_id);

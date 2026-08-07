-- Explicit recommendation email delivery log. Run after 008.

create table if not exists public.email_deliveries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  recommendation_id uuid not null references public.recommendations(id) on delete cascade,
  recipient_email text not null,
  status text not null default 'pending' check (status in ('pending', 'sent', 'failed')),
  provider text not null default 'resend',
  provider_message_id text,
  error_message text,
  attempt_count integer not null default 1 check (attempt_count between 1 and 10),
  created_at timestamptz not null default now(),
  sent_at timestamptz
);

create index if not exists email_deliveries_user_created_idx
  on public.email_deliveries(user_id, created_at desc);

create index if not exists email_deliveries_recommendation_idx
  on public.email_deliveries(recommendation_id, created_at desc);

alter table public.email_deliveries enable row level security;

drop policy if exists "Learners can read their own email deliveries" on public.email_deliveries;
create policy "Learners can read their own email deliveries"
  on public.email_deliveries for select
  using (auth.uid() = user_id);

drop policy if exists "Learners can create their own email deliveries" on public.email_deliveries;
create policy "Learners can create their own email deliveries"
  on public.email_deliveries for insert
  with check (auth.uid() = user_id);

drop policy if exists "Learners can update their own email deliveries" on public.email_deliveries;
create policy "Learners can update their own email deliveries"
  on public.email_deliveries for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
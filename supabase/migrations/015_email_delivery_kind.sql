-- Distinguish manual "email me this path" from scheduled weekly digests.

alter table public.email_deliveries
  add column if not exists delivery_kind text not null default 'manual'
  check (delivery_kind in ('manual', 'weekly_digest'));

create index if not exists email_deliveries_user_kind_sent_idx
  on public.email_deliveries(user_id, delivery_kind, sent_at desc);

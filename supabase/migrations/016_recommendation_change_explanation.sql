-- Store AI-grounded explanation when a path refreshes. Run after 015_email_delivery_kind.sql.

alter table public.recommendations
  add column if not exists change_explanation text;

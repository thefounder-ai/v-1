-- Vector indexing observability. Run after 003_admin_catalog.sql.

alter table public.products
  add column if not exists vector_sync_error text,
  add column if not exists vector_synced_at timestamptz,
  add column if not exists vector_sync_attempts integer not null default 0;

create index if not exists products_vector_sync_status_idx
  on public.products (vector_sync_status);
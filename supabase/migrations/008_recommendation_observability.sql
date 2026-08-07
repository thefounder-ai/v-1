-- Recommendation trace and retrieval metadata. Run after 007_recommendations.sql.

alter table public.recommendations
  add column if not exists trace_id text,
  add column if not exists retrieval_metadata jsonb not null default '{}'::jsonb;

create index if not exists recommendations_trace_id_idx
  on public.recommendations(trace_id)
  where trace_id is not null;

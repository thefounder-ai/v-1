-- Admin-only catalog writes. Run after 002_catalog.sql.

drop policy if exists "Admins can manage catalog items" on public.products;
create policy "Admins can manage catalog items"
  on public.products for all
  using (
    exists (
      select 1 from public.profiles
      where profiles.user_id = auth.uid()
        and profiles.role = 'admin'
    )
  )
  with check (
    exists (
      select 1 from public.profiles
      where profiles.user_id = auth.uid()
        and profiles.role = 'admin'
    )
  );

create or replace function public.set_products_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists products_updated_at on public.products;
create trigger products_updated_at
before update on public.products
for each row execute function public.set_products_updated_at();
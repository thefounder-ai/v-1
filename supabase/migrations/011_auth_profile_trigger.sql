-- Auto-create a learner profile when a Supabase Auth user is created.
-- Run after 001_profiles.sql. Removes reliance on app-side profile bootstrap alone.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (user_id, role, onboarding_complete)
  values (new.id, 'learner', false)
  on conflict (user_id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

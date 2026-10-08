-- Faculty Twin: Supabase schema.
-- Run once in the Supabase dashboard: SQL Editor -> New query -> paste -> Run.
-- Safe to re-run (everything is "if not exists" / "create or replace").
--
-- The backend talks to these tables only with the service role key, server side.
-- Row Level Security is enabled with no policies, so the anon key (never used by
-- this app) can read or write nothing.

-- ------------------------------------------------------------------ counters
-- Rate limits (per hashed visitor id), the daily voice character cap, login
-- attempts (per salted, daily-rotating hash), and daily question counts.
create table if not exists counters (
  key         text primary key,
  day         date not null default current_date,
  count       bigint not null default 0,
  expires_at  timestamptz,
  updated_at  timestamptz not null default now()
);
create index if not exists counters_expires_at on counters (expires_at);

-- Atomic add-with-cap. Adds p_amount to counter p_key unless the result would
-- pass p_cap (null = no cap). Returns whether the add happened and the count.
create or replace function ft_increment(
  p_key text,
  p_amount bigint,
  p_cap bigint default null,
  p_ttl_seconds integer default 172800
) returns table (allowed boolean, count bigint)
language plpgsql
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
  v bigint;
begin
  insert into counters (key, day, count, expires_at)
  values (p_key, current_date, 0, now() + make_interval(secs => p_ttl_seconds))
  on conflict (key) do nothing;

  update counters c
     set count = c.count + p_amount, updated_at = now()
   where c.key = p_key
     and (p_cap is null or c.count + p_amount <= p_cap)
  returning c.count into v;

  -- Opportunistic cleanup of expired rows (cheap; indexed).
  delete from counters where expires_at < now() - interval '1 day';

  if v is null then
    select c.count into v from counters c where c.key = p_key;
    return query select false, v;
  else
    return query select true, v;
  end if;
end;
$$;

revoke all on function ft_increment(text, bigint, bigint, integer) from public, anon, authenticated;
grant execute on function ft_increment(text, bigint, bigint, integer) to service_role;

-- ------------------------------------------------------------------ question log
-- Question text and scores only: no names, accounts, visitor ids, or IP addresses.
create table if not exists question_log (
  id          bigint generated always as identity primary key,
  at          timestamptz not null default now(),
  question    text not null check (char_length(question) <= 300),
  course      text,
  covered     boolean not null,
  top_score   real,
  provider    text,
  model       text,
  latency_ms  integer,
  kind        text  -- course_content, logistics, or null (not covered)
);
-- Added Oct 7 (logistics check): for tables made before the column existed.
alter table question_log add column if not exists kind text;
create index if not exists question_log_at on question_log (at desc);

-- ------------------------------------------------------------------ settings
-- Key/value rows written by the Settings page. Keys in use:
--   provider, model, voice_id (null = ELEVENLABS_VOICE_ID, "none" = captions only),
--   daily_voice_char_cap, student_passcode_hash (PBKDF2, never the passcode),
--   index_version (bumped by the local worker after it rebuilds the index).
create table if not exists settings (
  key         text primary key,
  value       jsonb,
  updated_at  timestamptz not null default now()
);

-- ------------------------------------------------------------------ courses, sessions, sources
create table if not exists courses (
  code        text primary key check (code ~ '^[0-9]{5}$'),
  title       text not null,
  term        text,
  created_at  timestamptz not null default now()
);

create table if not exists sessions (
  id          text primary key,                -- "<course>-s<NN>", e.g. 70445-s06
  course      text not null references courses (code) on delete cascade,
  session     integer not null check (session between 1 and 99),
  date        date,
  title       text,
  visible     boolean not null default true,  -- false hides it from students
  created_at  timestamptz not null default now(),
  unique (course, session)
);

-- One row per uploaded source file. Uploads go straight from the browser to
-- Storage (bucket twin-content, under inbox/<course>/s<NN>/<kind>/<file>).
-- Status: pending_upload (upload link minted, file not confirmed yet)
--   -> uploaded (file is in the bucket) -> processing -> ready | error.
-- The local processing worker (indexer/worker.py, on the machine that holds the
-- private archive) polls for status = 'uploaded' only, so it never sees a
-- half-uploaded file.
create table if not exists sources (
  id          bigint generated always as identity primary key,
  course      text not null references courses (code) on delete cascade,
  session     integer not null,
  kind        text not null check (kind in ('slides', 'transcript', 'video', 'notebook')),
  path        text not null unique,
  status      text not null default 'pending_upload'
              check (status in ('pending_upload', 'uploaded', 'processing', 'ready', 'error')),
  message     text,
  size_bytes  bigint,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists sources_status on sources (status);

-- ------------------------------------------------------------------ row level security
alter table counters     enable row level security;
alter table question_log enable row level security;
alter table settings     enable row level security;
alter table courses      enable row level security;
alter table sessions     enable row level security;
alter table sources      enable row level security;

-- ------------------------------------------------------------------ storage bucket
-- The content bucket must be private: everything in it is reached only through
-- short-lived signed links minted by the backend. Re-running this file forces
-- public = false even if someone flipped it in the dashboard. No storage
-- policies are created, so only the service role key can read or write.
-- Set the bucket's file size limit in the dashboard (Storage -> twin-content ->
-- Edit) to the largest class video you upload; see docs/SECURITY.md.
insert into storage.buckets (id, name, public)
values ('twin-content', 'twin-content', false)
on conflict (id) do update set public = false;

-- ------------------------------------------------------------------ seed
insert into courses (code, title, term) values
  ('70445', 'AI for Business Leaders', 'Fall 2026'),
  ('45884', 'AI Methods for Social and Visual Data', 'Fall 2026 Mini 1')
on conflict (code) do nothing;

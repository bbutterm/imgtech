-- История сравнений и согласование по листам.
--
-- До этой миграции результаты и отметки жили только в памяти вкладки:
-- перезагрузка страницы теряла работу целиком, вернуться к комплекту было
-- нельзя. Здесь появляются сама запись сравнения (с результатом и ссылками
-- на файлы) и отметки согласования — по листам, а не по отдельным отличиям.
--
-- Файлы хранятся не вечно: files_expire_at задаёт срок, после которого PDF
-- удаляются, а запись остаётся журналом (сводка, статусы, выгрузка отчёта).

create table if not exists public.comparisons (
    id              uuid primary key default gen_random_uuid(),
    uid             uuid not null references auth.users (id) on delete cascade,
    --задел под команды: сейчас всегда null, доступ идёт по uid
    org_id          uuid,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    file1_name      text not null check (char_length(file1_name) <= 300),
    file2_name      text not null check (char_length(file2_name) <= 300),
    --объекты в бакете uploads; null — файлы уже удалены (истёк срок либо
    --пользователь удалил сравнение вручную)
    file1_path      text check (char_length(file1_path) <= 500),
    file2_path      text check (char_length(file2_path) <= 500),
    files_expire_at timestamptz,
    state           text not null default 'running'
                    check (state in ('running', 'done', 'failed')),
    --план сопоставления и рамки отличий: ~0.5 КБ на лист, комплект из
    --50 листов — около 23 КБ, потолок в 100 рамок на лист — около 170 КБ
    result          jsonb check (result is null or pg_column_size(result) < 8 * 1024 * 1024),
    --компактная сводка (число листов с изменениями, отличий и т.п.):
    --список истории читает её и не тянет полный result каждой строки
    summary         jsonb check (summary is null or pg_column_size(summary) < 4096),
    error           text check (char_length(error) <= 2000)
);

create index if not exists comparisons_uid_idx
    on public.comparisons (uid, created_at desc);

create table if not exists public.sheet_reviews (
    comparison_id uuid not null references public.comparisons (id) on delete cascade,
    --идентификатор листа в том же виде, что в интерфейсе:
    --"11_17" для пары, "removed-15" и "added-13" для листов без пары
    sheet_id      text not null check (char_length(sheet_id) <= 64),
    approved      boolean not null default false,
    comment       text not null default '' check (char_length(comment) <= 2000),
    updated_at    timestamptz not null default now(),
    updated_by    uuid references auth.users (id),
    primary key (comparison_id, sheet_id)
);

alter table public.comparisons enable row level security;
alter table public.sheet_reviews enable row level security;

--доступ как у sessions: только свои строки
drop policy if exists comparisons_owner on public.comparisons;
create policy comparisons_owner on public.comparisons
    for all
    using (uid = auth.uid())
    with check (uid = auth.uid());

drop policy if exists sheet_reviews_owner on public.sheet_reviews;
create policy sheet_reviews_owner on public.sheet_reviews
    for all
    using (exists (select 1 from public.comparisons c
                   where c.id = comparison_id and c.uid = auth.uid()))
    with check (exists (select 1 from public.comparisons c
                        where c.id = comparison_id and c.uid = auth.uid()));

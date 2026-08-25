//Хранение сравнений и отметок согласования.
//
//До появления этого модуля результат и отметки жили только в памяти вкладки:
//перезагрузка теряла работу целиком. Пишем прямо из браузера — доступ
//ограничен политиками RLS («только свои строки»), поэтому отдельный
//серверный эндпоинт не нужен и лимит времени функции Vercel ни при чём.

import { supabase } from './supabase'

//сколько живут исходные PDF; после этого срока запись остаётся журналом:
//сводка, статусы согласования и выгрузка отчёта без просмотра разворотов
export const FILE_TTL_DAYS = 90
const BUCKET = 'uploads'
const DAY_MS = 24 * 60 * 60 * 1000

export function expiryFrom(now = new Date(), days = FILE_TTL_DAYS) {
  return new Date(now.getTime() + days * DAY_MS).toISOString()
}

export function filesAvailable(row, now = new Date()) {
  if (!row?.file1_path || !row?.file2_path) return false
  if (!row.files_expire_at) return true
  return new Date(row.files_expire_at).getTime() > now.getTime()
}

//позиции согласования: лист с изменениями (не каждое отличие!) и
//удалённые/добавленные листы. Общая для экрана просмотра и для истории,
//чтобы «согласовано N из M» считалось одинаково в обоих местах.
export function reviewSheets(result) {
  if (!result) return []
  const items = []
  for (const p of result.pages || []) {
    const total = p.boxes1.length + p.boxes2.length +
                  p.textBoxes1.length + p.textBoxes2.length
    const uncomparable = p.comparable === false
    if (total === 0 && !p.heavilyChanged && !uncomparable) continue
    items.push({
      id: `${p.index1}_${p.index2}`,
      sort: p.index1,
      page: p.index1 === p.index2
        ? `${p.index1 + 1}`
        : `${p.index1 + 1} ↔ ${p.index2 + 1}`,
      type: uncomparable
        ? 'не удалось сопоставить — проверить вручную'
        : (p.heavilyChanged ? 'сильно изменена' : `отличий: ${total}`),
    })
  }
  for (const i of result.removed || []) {
    items.push({ id: `removed-${i}`, sort: i, page: `${i + 1} (док. 1)`, type: 'лист удалён' })
  }
  for (const j of result.added || []) {
    items.push({ id: `added-${j}`, sort: j, page: `${j + 1} (док. 2)`, type: 'лист добавлен' })
  }
  return items.sort((a, b) => a.sort - b.sort)
}

export function summarize(result) {
  const pages = result?.pages || []
  const diffs = pages.reduce((n, p) => n +
    p.boxes1.length + p.boxes2.length + p.textBoxes1.length + p.textBoxes2.length, 0)
  return {
    sheets: reviewSheets(result).length,
    diffs,
    removed: (result?.removed || []).length,
    added: (result?.added || []).length,
    heavy: pages.filter((p) => p.heavilyChanged).length,
    uncomparable: pages.filter((p) => p.comparable === false).length,
    numPages1: result?.numPages1 || 0,
    numPages2: result?.numPages2 || 0,
  }
}

function unwrap({ data, error }) {
  if (error) throw new Error(error.message)
  return data
}

export async function createComparison(uid, file1, file2) {
  return unwrap(await supabase.from('comparisons')
    .insert({ uid, file1_name: file1.name, file2_name: file2.name, state: 'running' })
    .select().single())
}

export async function attachFiles(id, path1, path2) {
  return unwrap(await supabase.from('comparisons')
    .update({ file1_path: path1, file2_path: path2, files_expire_at: expiryFrom(),
              updated_at: new Date().toISOString() })
    .eq('id', id).select().single())
}

export async function finishComparison(id, result) {
  return unwrap(await supabase.from('comparisons')
    .update({ state: 'done', result, summary: summarize(result),
              updated_at: new Date().toISOString() })
    .eq('id', id).select().single())
}

export async function failComparison(id, message) {
  return unwrap(await supabase.from('comparisons')
    .update({ state: 'failed', error: String(message).slice(0, 2000),
              updated_at: new Date().toISOString() })
    .eq('id', id).select().single())
}

//список без поля result: полные результаты тяжелее сводки в десятки раз
const LIST_COLUMNS =
  'id, created_at, file1_name, file2_name, file1_path, file2_path, ' +
  'files_expire_at, state, summary, error'

export async function listComparisons(limit = 50) {
  const rows = unwrap(await supabase.from('comparisons').select(LIST_COLUMNS)
    .order('created_at', { ascending: false }).limit(limit)) || []
  //одним запросом — сколько листов согласовано в каждом сравнении
  const approved = unwrap(await supabase.from('sheet_reviews')
    .select('comparison_id').eq('approved', true)) || []
  const counts = approved.reduce((acc, r) => {
    acc[r.comparison_id] = (acc[r.comparison_id] || 0) + 1
    return acc
  }, {})
  return rows.map((row) => ({ ...row, approvedCount: counts[row.id] || 0 }))
}

export async function loadComparison(id) {
  return unwrap(await supabase.from('comparisons').select('*').eq('id', id).single())
}

export async function loadReviews(id) {
  const rows = unwrap(await supabase.from('sheet_reviews')
    .select('sheet_id, approved, comment').eq('comparison_id', id)) || []
  return rows.reduce((acc, r) => {
    acc[r.sheet_id] = { approved: r.approved, comment: r.comment || '' }
    return acc
  }, {})
}

export async function saveReview(comparisonId, sheetId, review, uid) {
  return unwrap(await supabase.from('sheet_reviews').upsert({
    comparison_id: comparisonId,
    sheet_id: sheetId,
    approved: !!review.approved,
    comment: review.comment || '',
    updated_at: new Date().toISOString(),
    updated_by: uid,
  }, { onConflict: 'comparison_id,sheet_id' }))
}

export async function signedUrls(row, seconds = 3600) {
  const [s1, s2] = await Promise.all([
    supabase.storage.from(BUCKET).createSignedUrl(row.file1_path, seconds),
    supabase.storage.from(BUCKET).createSignedUrl(row.file2_path, seconds),
  ])
  if (s1.error || s2.error) throw new Error('Не удалось получить файлы сравнения')
  return [s1.data.signedUrl, s2.data.signedUrl]
}

export async function removeFiles(row) {
  const paths = [row.file1_path, row.file2_path].filter(Boolean)
  if (paths.length) await supabase.storage.from(BUCKET).remove(paths)
}

export async function deleteComparison(row) {
  await removeFiles(row)
  //строки sheet_reviews уходят каскадом
  unwrap(await supabase.from('comparisons').delete().eq('id', row.id))
}

//истёкшие файлы удаляем при открытии списка: планировщика в проекте нет,
//а владелец строки по RLS может убрать свои объекты сам
export async function purgeExpired(rows, now = new Date()) {
  const stale = rows.filter((r) => r.file1_path && !filesAvailable(r, now))
  for (const row of stale) {
    try {
      await removeFiles(row)
      unwrap(await supabase.from('comparisons')
        .update({ file1_path: null, file2_path: null }).eq('id', row.id))
      row.file1_path = null
      row.file2_path = null
    } catch (err) {
      console.warn('Не удалось убрать просроченные файлы', err)
    }
  }
  return rows
}

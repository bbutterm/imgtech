//Тесты слоя хранения сравнений. Запуск: cd frontend && npm test
import { beforeEach, describe, expect, it, vi } from 'vitest'

//клиент Supabase подменяем: тесты проверяют нашу логику, а не сеть
const state = { tables: {}, storage: { removed: [] } }

function makeQuery(table) {
  const q = {
    _filters: {},
    select: vi.fn(() => q),
    order: vi.fn(() => q),
    limit: vi.fn(() => Promise.resolve({ data: state.tables[table] || [], error: null })),
    eq: vi.fn((col, val) => { q._filters[col] = val; return q }),
    insert: vi.fn((row) => { state.tables[table] = [...(state.tables[table] || []), row]; return q }),
    update: vi.fn((patch) => { q._patch = patch; return q }),
    upsert: vi.fn((row) => { state.upserted = row; return Promise.resolve({ data: null, error: null }) }),
    delete: vi.fn(() => { state.deleted = q._filters; return Promise.resolve({ data: null, error: null }) }),
    single: vi.fn(() => Promise.resolve({ data: { ...q._filters, ...(q._patch || {}) }, error: null })),
    then: (resolve) => resolve({ data: state.tables[table] || [], error: null }),
  }
  return q
}

vi.mock('./supabase', () => ({
  supabase: {
    from: (table) => makeQuery(table),
    storage: {
      from: () => ({
        remove: (paths) => { state.storage.removed.push(...paths); return Promise.resolve({ error: null }) },
        createSignedUrl: (path) => Promise.resolve({ data: { signedUrl: `https://x/${path}` }, error: null }),
      }),
    },
  },
}))

const {
  FILE_TTL_DAYS, expiryFrom, filesAvailable, reviewSheets, summarize,
  listComparisons, purgeExpired, signedUrls,
} = await import('./comparisons')

const page = (over = {}) => ({
  index1: 0, index2: 0, boxes1: [], boxes2: [], textBoxes1: [], textBoxes2: [],
  heavilyChanged: false, comparable: true, ...over,
})

beforeEach(() => { state.tables = {}; state.storage.removed = [] })

describe('срок хранения файлов', () => {
  it('свежая запись — файлы доступны', () => {
    const row = { file1_path: 'a', file2_path: 'b', files_expire_at: expiryFrom() }
    expect(filesAvailable(row)).toBe(true)
  })

  it('после истечения срока — недоступны', () => {
    const past = new Date(Date.now() - 1000).toISOString()
    const row = { file1_path: 'a', file2_path: 'b', files_expire_at: past }
    expect(filesAvailable(row)).toBe(false)
  })

  it('удалённые файлы — недоступны, даже если срок не истёк', () => {
    expect(filesAvailable({ file1_path: null, file2_path: null,
                            files_expire_at: expiryFrom() })).toBe(false)
  })

  it('срок отсчитывается на FILE_TTL_DAYS вперёд', () => {
    const now = new Date('2026-01-01T00:00:00Z')
    const days = (new Date(expiryFrom(now)) - now) / 86400000
    expect(days).toBe(FILE_TTL_DAYS)
  })
})

describe('список листов для согласования', () => {
  it('лист без отличий в список не попадает', () => {
    expect(reviewSheets({ pages: [page()] })).toEqual([])
  })

  it('согласование идёт по листам, а не по отдельным отличиям', () => {
    const result = { pages: [page({ boxes1: [[0, 0, 1, 1]], textBoxes2: [[2, 2, 3, 3]] })] }
    const sheets = reviewSheets(result)
    expect(sheets).toHaveLength(1)
    expect(sheets[0].type).toBe('отличий: 2')
  })

  it('несопоставленный лист требует ручной проверки', () => {
    const sheets = reviewSheets({ pages: [page({ comparable: false })] })
    expect(sheets[0].type).toContain('не удалось сопоставить')
  })

  it('удалённые и добавленные листы включены и отсортированы', () => {
    const sheets = reviewSheets({ pages: [], removed: [4], added: [1] })
    expect(sheets.map((s) => s.id)).toEqual(['added-1', 'removed-4'])
  })

  it('идентификатор листа совпадает с ключом пары в интерфейсе', () => {
    const sheets = reviewSheets({ pages: [page({ index1: 11, index2: 17, boxes1: [[0, 0, 1, 1]] })] })
    expect(sheets[0].id).toBe('11_17')
    expect(sheets[0].page).toBe('12 ↔ 18')
  })
})

describe('сводка для списка истории', () => {
  it('считает листы, отличия и особые состояния', () => {
    const s = summarize({
      pages: [page({ boxes1: [[0, 0, 1, 1]] }), page({ index1: 1, index2: 1, heavilyChanged: true }),
              page({ index1: 2, index2: 2, comparable: false })],
      removed: [7], added: [], numPages1: 10, numPages2: 9,
    })
    expect(s).toMatchObject({ sheets: 4, diffs: 1, heavy: 1, uncomparable: 1,
                              removed: 1, numPages1: 10 })
  })

  it('сводка на порядки меньше полного результата', () => {
    const pages = Array.from({ length: 50 }, (_, i) =>
      page({ index1: i, index2: i, boxes1: Array.from({ length: 100 }, () => [1, 2, 3, 4]) }))
    const result = { pages, removed: [], added: [] }
    const full = JSON.stringify(result).length
    const brief = JSON.stringify(summarize(result)).length
    expect(brief).toBeLessThan(full / 100)
  })
})

describe('очистка просроченных файлов', () => {
  it('удаляет объекты и обнуляет пути', async () => {
    const rows = [
      { id: '1', file1_path: 'u/1/1.pdf', file2_path: 'u/1/2.pdf',
        files_expire_at: new Date(Date.now() - 1000).toISOString() },
      { id: '2', file1_path: 'u/2/1.pdf', file2_path: 'u/2/2.pdf',
        files_expire_at: expiryFrom() },
    ]
    await purgeExpired(rows)
    expect(state.storage.removed).toEqual(['u/1/1.pdf', 'u/1/2.pdf'])
    expect(rows[0].file1_path).toBeNull()
    expect(rows[1].file1_path).toBe('u/2/1.pdf')
  })
})

describe('список сравнений', () => {
  it('добавляет число согласованных листов к каждой записи', async () => {
    state.tables.comparisons = [{ id: 'c1' }, { id: 'c2' }]
    state.tables.sheet_reviews = [
      { comparison_id: 'c1' }, { comparison_id: 'c1' }, { comparison_id: 'c2' },
    ]
    const rows = await listComparisons()
    expect(rows.find((r) => r.id === 'c1').approvedCount).toBe(2)
    expect(rows.find((r) => r.id === 'c2').approvedCount).toBe(1)
  })
})

describe('ссылки на файлы', () => {
  it('подписывает оба файла записи', async () => {
    const urls = await signedUrls({ file1_path: 'u/1/1.pdf', file2_path: 'u/1/2.pdf' })
    expect(urls).toEqual(['https://x/u/1/1.pdf', 'https://x/u/1/2.pdf'])
  })
})

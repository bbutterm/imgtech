// Прогон интерфейса в настоящем браузере с подменой Supabase.
//
// Проверяет то, что юнит-тесты не видят: историю сравнений, открытие
// сохранённой записи и журнал согласования для сравнения с истёкшим сроком
// хранения файлов. Сеть до Supabase из CI-контейнера закрыта, поэтому
// ответы auth/rest подменяются перехватом маршрутов.
//
//   cd frontend && npm run build && npm run preview -- --port 4173 &
//   npm run test:ui
//playwright намеренно не в зависимостях проекта: он нужен только этому
//прогону и обычно уже установлен глобально (в CI-контейнере — точно)
async function loadChromium() {
  try {
    return (await import('playwright')).chromium
  } catch {
    const { execSync } = await import('node:child_process')
    const root = execSync('npm root -g').toString().trim()
    return (await import(`${root}/playwright/index.mjs`)).chromium
  }
}
const chromium = await loadChromium()

const BASE = process.env.BASE || 'http://127.0.0.1:4173'
const USER = { id: 'u-1', email: 'demo@example.com', aud: 'authenticated', role: 'authenticated' }
const SESSION = {
  access_token: 'fake-token', token_type: 'bearer', expires_in: 3600,
  expires_at: Math.floor(Date.now() / 1000) + 3600, refresh_token: 'r', user: USER,
}
const RESULT = {
  numPages1: 3, numPages2: 3, totalPairs: 3, elapsed: 4.2, removed: [2], added: [],
  pages: [
    { index1: 0, index2: 0, boxes1: [[10, 10, 20, 20]], boxes2: [], textBoxes1: [], textBoxes2: [],
      width1: 2384, height1: 1684, width2: 2384, height2: 1684,
      unmatchedShare: 0, heavilyChanged: false, comparable: true, registeredAngle: 0, truncated: false },
    { index1: 1, index2: 1, boxes1: [], boxes2: [], textBoxes1: [], textBoxes2: [],
      width1: 2384, height1: 1684, width2: 2384, height2: 1684,
      unmatchedShare: 0.81, heavilyChanged: true, comparable: false, registeredAngle: 0, truncated: false },
  ],
}
const ROW = {
  id: 'c-1', created_at: '2026-08-20T09:30:00Z', uid: USER.id,
  file1_name: 'Тульская_рев1.pdf', file2_name: 'Тульская_рев2.pdf',
  file1_path: null, file2_path: null,               // файлы удалены по сроку
  files_expire_at: '2026-05-01T00:00:00Z', state: 'done', error: null,
  summary: { sheets: 3, diffs: 1, heavy: 1, uncomparable: 1, removed: 1, added: 0 },
  result: RESULT,
}

const browser = await chromium.launch()
const page = await browser.newPage()
const problems = []
page.on('console', (m) => { if (m.type() === 'error') problems.push(m.text()) })
page.on('pageerror', (e) => problems.push(String(e)))

const json = (body) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
await page.route('**/auth/v1/token**', (r) => r.fulfill(json(SESSION)))
await page.route('**/auth/v1/user**', (r) => r.fulfill(json(USER)))
await page.route('**/rest/v1/sessions**', (r) => r.fulfill(json([])))
await page.route('**/rest/v1/sheet_reviews**', (r) => r.fulfill(json([])))
await page.route('**/rest/v1/comparisons**', (r) => {
  const url = r.request().url()
  //запрос одной записи (открытие) отличается фильтром по id
  if (url.includes('id=eq.')) return r.fulfill(json(ROW))
  return r.fulfill(json([ROW]))
})
await page.route('**/realtime/**', (r) => r.abort())

await page.goto(BASE)
await page.fill('input[type=email]', 'demo@example.com')
await page.fill('input[type=password]', 'secret')
await page.click('button[type=submit]')

const check = async (name, fn) => {
  try { await fn(); console.log(`  [ok] ${name}`) }
  catch (e) { console.log(`  [FAIL] ${name} — ${e.message.split('\n')[0]}`); problems.push(name) }
}

await page.waitForSelector('.history', { timeout: 15000 })
await check('история показывает сохранённое сравнение', async () => {
  await page.waitForSelector('text=Тульская_рев1.pdf', { timeout: 5000 })
})
await check('видно число листов и согласованных', async () => {
  const meta = await page.textContent('.history-meta')
  if (!meta.includes('листов с изменениями: 3')) throw new Error(meta)
  if (!meta.includes('согласовано: 0')) throw new Error(meta)
})
await check('просроченные файлы отмечены', async () => {
  const meta = await page.textContent('.history-meta')
  if (!meta.includes('сроку хранения')) throw new Error(meta)
})
await check('кнопка называется «Отчёт», а не «Открыть»', async () => {
  const label = await page.textContent('.history-actions button')
  if (label.trim() !== 'Отчёт') throw new Error(label)
})

await page.click('.history-actions button')
await check('открывается журнал согласования', async () => {
  await page.waitForSelector('.journal-table', { timeout: 8000 })
})
await check('в журнале строки по листам, включая несопоставленный', async () => {
  const rows = await page.$$eval('.journal-table tbody tr', (tr) => tr.map((r) => r.innerText))
  if (rows.length !== 3) throw new Error(`строк ${rows.length}: ${JSON.stringify(rows)}`)
  if (!rows.some((r) => r.includes('не удалось сопоставить'))) throw new Error(JSON.stringify(rows))
  if (!rows.some((r) => r.includes('лист удалён'))) throw new Error(JSON.stringify(rows))
})
await check('сводка учитывает несопоставленные листы', async () => {
  const summary = await page.textContent('.summary')
  if (!summary.includes('не удалось сопоставить листов: 1')) throw new Error(summary)
})
await check('выгрузка отчёта доступна', async () => {
  await page.waitForSelector('.export-btn', { timeout: 3000 })
})
if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT, fullPage: true })

await browser.close()
const real = problems.filter((p) => !/Failed to load resource|realtime|WebSocket/i.test(p))
if (real.length) { console.log('\nПРОБЛЕМЫ:', real.slice(0, 5)); process.exit(1) }
console.log('\nПрогон интерфейса пройден.')

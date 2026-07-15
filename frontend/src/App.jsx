import { useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { supabase } from './supabase'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const VIEW_W = 620          // ширина вьюпорта страницы, px
const DIRECT_LIMIT_MB = 3.5 // мельче — напрямую в API, крупнее — через хранилище
const MAX_FILE_MB = 50      // лимит Supabase Storage на один файл
const BATCH_SIZE = 6        // пар страниц на один запрос к API

const mb = (bytes) => (bytes / 1024 / 1024).toFixed(2)
const pairKeyOf = (p) => `${p.index1}_${p.index2}`

const sidKey = (uid) => `imgtech-sid:${uid}`

//записывает ID новой активной сессии — все остальные устройства этого
//пользователя увидят изменение и разлогинятся
function claimSession(uid) {
  const sid = crypto.randomUUID()
  localStorage.setItem(sidKey(uid), sid)
  supabase.from('sessions').upsert({ uid, sid }).then(() => {}, () => {})
}

function Dropzone({ label, file, onFile }) {
  const [drag, setDrag] = useState(false)
  const inputRef = useRef(null)
  return (
    <div
      className={`dropzone${drag ? ' drag' : ''}${file ? ' filled' : ''}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDrag(false)
        const f = e.dataTransfer.files?.[0]
        if (f) onFile(f)
      }}
    >
      <input
        ref={inputRef} type="file" accept=".pdf,application/pdf" hidden
        onChange={(e) => onFile(e.target.files?.[0] || null)}
      />
      <div className="dz-label">{label}</div>
      {file
        ? <div className="dz-file">{file.name} <span>· {mb(file.size)} МБ</span></div>
        : <div className="dz-hint">перетащите PDF или нажмите</div>}
    </div>
  )
}

function Progress({ stage, done, total, elapsed }) {
  return (
    <div className="progress">
      <div className="spinner" />
      <div className="progress-info">
        <div className="stage">
          {stage}{total > 0 && ` — ${done} из ${total} страниц`}
        </div>
        {total > 0 && (
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${(100 * done) / total}%` }} />
          </div>
        )}
        <div className="elapsed">{elapsed.toFixed(0)} с</div>
      </div>
    </div>
  )
}

//монтирует содержимое только когда блок докручен до зоны видимости —
//иначе десятки страниц в канвасах высокого разрешения кладут вкладку
function LazyMount({ height, force, children }) {
  const ref = useRef(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (force) setVisible(true)
  }, [force])

  useEffect(() => {
    if (visible) return
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => { if (entries.some((x) => x.isIntersecting)) setVisible(true) },
      { rootMargin: '500px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [visible])

  return (
    <div ref={ref} style={visible ? undefined : { minHeight: height }}>
      {visible ? children : null}
    </div>
  )
}

function PageView({ pdfDoc, pageNumber, boxes, textBoxes, pageW, pageH,
                    view, setView, label, activeId, side, pairKey, renderW }) {
  const canvasRef = useRef(null)
  const vpRef = useRef(null)
  const dragRef = useRef(null)

  const cssScale = VIEW_W / pageW
  const viewH = VIEW_W * (pageH / pageW)

  useEffect(() => {
    if (!pdfDoc) return
    let cancelled = false
    ;(async () => {
      const page = await pdfDoc.getPage(pageNumber)
      const base = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: renderW / base.width })
      const canvas = canvasRef.current
      if (!canvas || cancelled) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      canvas.style.width = `${VIEW_W}px`
      canvas.style.height = 'auto'
      await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise
    })()
    return () => { cancelled = true }
  }, [pdfDoc, pageNumber, renderW])

  useEffect(() => {
    const el = vpRef.current
    if (!el) return
    const onWheel = (e) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const vx = e.clientX - rect.left
      const vy = e.clientY - rect.top
      setView((v) => {
        const z = Math.min(24, Math.max(1, v.z * Math.pow(1.0015, -e.deltaY)))
        const cx = (vx - v.tx) / v.z
        const cy = (vy - v.ty) / v.z
        return { z, tx: vx - cx * z, ty: vy - cy * z }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [setView])

  const onPointerDown = (e) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { x: e.clientX, y: e.clientY }
  }
  const onPointerMove = (e) => {
    if (!dragRef.current) return
    const dx = e.clientX - dragRef.current.x
    const dy = e.clientY - dragRef.current.y
    dragRef.current = { x: e.clientX, y: e.clientY }
    setView((v) => ({ ...v, tx: v.tx + dx, ty: v.ty + dy }))
  }
  const onPointerUp = () => { dragRef.current = null }

  const border = Math.max(0.4, 2.5 / view.z)
  const boxStyle = (b) => ({
    left: b[0] * cssScale,
    top: b[1] * cssScale,
    width: (b[2] - b[0]) * cssScale,
    height: (b[3] - b[1]) * cssScale,
    borderWidth: border,
  })

  return (
    <div className="page-view">
      <div className="page-label">{label}</div>
      <div
        className="viewer" ref={vpRef} style={{ width: VIEW_W, height: viewH }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      >
        <div
          className="viewer-content"
          style={{ transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.z})` }}
        >
          <canvas ref={canvasRef} />
          {boxes.map((b, i) => {
            const id = `${side}-graphics-${pairKey}-${i}`
            return <div key={id} className={`box box-graphics${id === activeId ? ' active' : ''}`} style={boxStyle(b)} />
          })}
          {textBoxes.map((b, i) => {
            const id = `${side}-text-${pairKey}-${i}`
            return <div key={id} className={`box box-text${id === activeId ? ' active' : ''}`} style={boxStyle(b)} />
          })}
        </div>
        {view.z > 1.01 && (
          <button className="fit-btn" title="Показать весь лист"
                  onClick={(e) => { e.stopPropagation(); setView({ z: 1, tx: 0, ty: 0 }) }}>
            весь лист
          </button>
        )}
      </div>
    </div>
  )
}

function PagePair({ page, docs, focus, activeId, renderW }) {
  const [view, setView] = useState({ z: 1, tx: 0, ty: 0 })
  const secRef = useRef(null)
  const pairKey = pairKeyOf(page)
  const focused = focus?.pairKey === pairKey

  useEffect(() => {
    if (!focused || !focus.box) return
    secRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    const cssScale = VIEW_W / page.width1
    const viewH = VIEW_W * (page.height1 / page.width1)
    const b = focus.box
    const bcx = ((b[0] + b[2]) / 2) * cssScale
    const bcy = ((b[1] + b[3]) / 2) * cssScale
    const bw = Math.max((b[2] - b[0]) * cssScale, 8)
    const bh = Math.max((b[3] - b[1]) * cssScale, 8)
    const z = Math.min(16, Math.max(1.2, 0.5 * Math.min(VIEW_W / bw, viewH / bh)))
    setView({ z, tx: VIEW_W / 2 - bcx * z, ty: viewH / 2 - bcy * z })
  }, [focus])

  const total =
    page.boxes1.length + page.boxes2.length +
    page.textBoxes1.length + page.textBoxes2.length

  const title = page.index1 === page.index2
    ? `Страница ${page.index1 + 1}`
    : `Страницы ${page.index1 + 1} ↔ ${page.index2 + 1}`

  const showPair = total > 0 || page.heavilyChanged
  const estHeight = VIEW_W * (page.height1 / page.width1) + 40

  return (
    <section className="page-pair" ref={secRef}>
      <h3>
        {title}
        {total === 0 && !page.heavilyChanged &&
          <span className="badge badge-ok">отличий не найдено</span>}
        {total > 0 && <span className="badge badge-diff">{total} отличий</span>}
        {page.heavilyChanged && (
          <span className="badge badge-warn">
            страница сильно изменена — рамки не показываются
          </span>
        )}
        {page.truncated && (
          <span className="badge badge-warn">
            изменений очень много — показаны крупнейшие
          </span>
        )}
      </h3>
      {showPair && (
        <LazyMount height={estHeight} force={focused}>
          <div className="pair-row">
            <PageView
              pdfDoc={docs.doc1} pageNumber={page.index1 + 1} side={1} pairKey={pairKey}
              boxes={page.boxes1} textBoxes={page.textBoxes1}
              pageW={page.width1} pageH={page.height1} renderW={renderW}
              view={view} setView={setView} activeId={activeId}
              label={`Документ 1 — стр. ${page.index1 + 1}`}
            />
            <PageView
              pdfDoc={docs.doc2} pageNumber={page.index2 + 1} side={2} pairKey={pairKey}
              boxes={page.boxes2} textBoxes={page.textBoxes2}
              pageW={page.width2} pageH={page.height2} renderW={renderW}
              view={view} setView={setView} activeId={activeId}
              label={`Документ 2 — стр. ${page.index2 + 1}`}
            />
          </div>
        </LazyMount>
      )}
    </section>
  )
}

function Login({ notice }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { data, error: err } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      })
      if (err) throw err
      claimSession(data.user.id)
    } catch (err) {
      const msg = String(err?.message || '')
      if (msg.includes('Invalid login credentials')) {
        setError('Неверный email или пароль')
      } else if (msg.includes('Email not confirmed')) {
        setError('Аккаунт не подтверждён — обратитесь к администратору')
      } else if (msg.toLowerCase().includes('rate limit') || err?.status === 429) {
        setError('Слишком много попыток входа — подождите пару минут')
      } else if (msg.includes('fetch') || msg.includes('network')) {
        setError('Нет связи с сервером авторизации — проверьте соединение')
      } else {
        setError('Не удалось войти, попробуйте ещё раз')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h2>Вход</h2>
        <p className="login-hint">Доступ по приглашению. Введите выданные вам email и пароль.</p>
        {notice && <div className="notice">{notice}</div>}
        <label>
          Email
          <input type="email" autoComplete="username" required value={email}
                 onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Пароль
          <input type="password" autoComplete="current-password" required value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="error">{error}</div>}
        <button type="submit" disabled={busy}>{busy ? 'Вхожу…' : 'Войти'}</button>
      </form>
    </main>
  )
}

function Workspace({ user }) {
  const [file1, setFile1] = useState(null)
  const [file2, setFile2] = useState(null)
  const [docs, setDocs] = useState(null)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('')
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState('')
  const [cur, setCur] = useState(0)
  const [focus, setFocus] = useState(null)

  useEffect(() => {
    if (!busy) return
    const t0 = performance.now()
    const timer = setInterval(() => setElapsed((performance.now() - t0) / 1000), 250)
    return () => clearInterval(timer)
  }, [busy])

  const tooBig = Math.max(file1?.size || 0, file2?.size || 0) > MAX_FILE_MB * 1024 * 1024

  const diffs = useMemo(() => {
    if (!result) return []
    const list = []
    for (const p of result.pages) {
      const key = pairKeyOf(p)
      p.boxes1.forEach((box, i) => list.push({ id: `1-graphics-${key}-${i}`, pairKey: key, page: p.index1, side: 1, type: 'графика', box }))
      p.boxes2.forEach((box, i) => list.push({ id: `2-graphics-${key}-${i}`, pairKey: key, page: p.index2, side: 2, type: 'графика', box }))
      p.textBoxes1.forEach((box, i) => list.push({ id: `1-text-${key}-${i}`, pairKey: key, page: p.index1, side: 1, type: 'текст', box }))
      p.textBoxes2.forEach((box, i) => list.push({ id: `2-text-${key}-${i}`, pairKey: key, page: p.index2, side: 2, type: 'текст', box }))
    }
    return list
  }, [result])

  //страницы, удалённые/добавленные листы — единый сортированный список
  const entries = useMemo(() => {
    if (!result) return []
    const list = result.pages.map((p) => ({ kind: 'pair', sort: p.index1, p }))
    for (const i of result.removed || []) list.push({ kind: 'removed', sort: i - 0.1, i })
    for (const j of result.added || []) list.push({ kind: 'added', sort: j + 0.1, j })
    return list.sort((a, b) => a.sort - b.sort)
  }, [result])

  const graphicsCount = diffs.filter((d) => d.type === 'графика').length
  const textCount = diffs.length - graphicsCount
  const heavyCount = result ? result.pages.filter((p) => p.heavilyChanged).length : 0
  const removedCount = result?.removed?.length || 0
  const addedCount = result?.added?.length || 0
  const totalIssues = diffs.length + removedCount + addedCount + heavyCount
  const renderW = (result?.totalPairs || 0) > 8 ? 1600 : 2800

  function goTo(idx) {
    if (!diffs.length) return
    const i = (idx + diffs.length) % diffs.length
    setCur(i)
    const d = diffs[i]
    setFocus({ pairKey: d.pairKey, box: d.box, seq: Date.now() })
  }

  async function handleCompare(e) {
    e.preventDefault()
    if (!file1 || !file2) {
      setError('Выберите оба PDF-файла')
      return
    }
    setBusy(true)
    setElapsed(0)
    setError('')
    setResult(null)
    setDocs(null)
    setFocus(null)
    setCur(0)
    setProgress({ done: 0, total: 0 })
    setStage('Подготовка файлов')

    let cleanup = () => {}
    try {
      const { data: sessionData } = await supabase.auth.getSession()
      const token = sessionData.session?.access_token
      if (!token) throw new Error('Сессия истекла — обновите страницу и войдите заново.')
      const authH = { Authorization: `Bearer ${token}` }

      //транспорт: мелкие файлы ходят в API напрямую, крупные — через хранилище
      let transport
      if (file1.size + file2.size < DIRECT_LIMIT_MB * 1024 * 1024) {
        const formData = (extra) => {
          const fd = new FormData()
          fd.append('file1', file1)
          fd.append('file2', file2)
          if (extra) fd.append('pairs', extra)
          return fd
        }
        transport = {
          plan: () => fetch('/api/plan', { method: 'POST', body: formData(), headers: authH }),
          batch: (pairs) => fetch('/api/compare-batch', {
            method: 'POST', body: formData(JSON.stringify(pairs)), headers: authH,
          }),
        }
      } else {
        setStage('Загрузка файлов в хранилище')
        const jobId = crypto.randomUUID()
        const path1 = `${user.id}/${jobId}/1.pdf`
        const path2 = `${user.id}/${jobId}/2.pdf`
        cleanup = () => supabase.storage.from('uploads').remove([path1, path2]).then(() => {}, () => {})
        const opts = { contentType: 'application/pdf' }
        const [up1, up2] = await Promise.all([
          supabase.storage.from('uploads').upload(path1, file1, opts),
          supabase.storage.from('uploads').upload(path2, file2, opts),
        ])
        if (up1.error || up2.error) {
          throw new Error(`Не удалось загрузить файлы: ${(up1.error || up2.error).message}`)
        }
        const [s1, s2] = await Promise.all([
          supabase.storage.from('uploads').createSignedUrl(path1, 3600),
          supabase.storage.from('uploads').createSignedUrl(path2, 3600),
        ])
        if (s1.error || s2.error) throw new Error('Не удалось подготовить файлы к сравнению')
        const urls = { url1: s1.data.signedUrl, url2: s2.data.signedUrl }
        const jsonH = { ...authH, 'Content-Type': 'application/json' }
        transport = {
          plan: () => fetch('/api/plan-urls', {
            method: 'POST', headers: jsonH, body: JSON.stringify(urls),
          }),
          batch: (pairs) => fetch('/api/compare-batch-urls', {
            method: 'POST', headers: jsonH, body: JSON.stringify({ ...urls, pairs }),
          }),
        }
      }

      const fail = async (resp) => {
        if (resp.status === 401) return 'Сессия истекла — обновите страницу и войдите заново.'
        let msg = `Ошибка сервера (${resp.status})`
        try { msg = (await resp.json()).error || msg } catch { /* not json */ }
        return msg
      }

      setStage('Сопоставление страниц')
      const planResp = await transport.plan()
      if (!planResp.ok) throw new Error(await fail(planResp))
      const plan = await planResp.json()

      //документы в просмотрщик — результат дорисовывается по мере партий
      const [buf1, buf2] = await Promise.all([file1.arrayBuffer(), file2.arrayBuffer()])
      const [doc1, doc2] = await Promise.all([
        pdfjsLib.getDocument({ data: buf1 }).promise,
        pdfjsLib.getDocument({ data: buf2 }).promise,
      ])
      setDocs({ doc1, doc2 })
      setResult({
        pages: [],
        removed: plan.removed,
        added: plan.added,
        numPages1: plan.numPages1,
        numPages2: plan.numPages2,
        totalPairs: plan.pairs.length,
        elapsed: 0,
      })
      setProgress({ done: 0, total: plan.pairs.length })
      setStage('Сравнение')

      const t0 = performance.now()
      for (let k = 0; k < plan.pairs.length; k += BATCH_SIZE) {
        const chunk = plan.pairs.slice(k, k + BATCH_SIZE)
        const resp = await transport.batch(chunk)
        if (!resp.ok) throw new Error(await fail(resp))
        const data = await resp.json()
        setResult((prev) => prev && ({ ...prev, pages: [...prev.pages, ...data.pages] }))
        setProgress({ done: Math.min(k + chunk.length, plan.pairs.length), total: plan.pairs.length })
      }
      setResult((prev) => prev && ({ ...prev, elapsed: Math.round((performance.now() - t0) / 100) / 10 }))
    } catch (err) {
      if (err instanceof TypeError) {
        setError('Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.')
      } else {
        setError(String(err.message || err))
      }
    } finally {
      cleanup()
      setBusy(false)
    }
  }

  return (
      <main>
        <form onSubmit={handleCompare} className="upload-form">
          <Dropzone label="Документ 1" file={file1} onFile={setFile1} />
          <Dropzone label="Документ 2" file={file2} onFile={setFile2} />
          <div className="form-actions">
            <button type="submit" disabled={busy || tooBig}>
              {busy ? 'Сравниваю…' : 'Сравнить'}
            </button>
            {tooBig && (
              <div className="size-warn">
                Файл больше {MAX_FILE_MB} МБ — такой размер пока не поддерживается.
              </div>
            )}
          </div>
        </form>

        {busy && <Progress stage={stage} done={progress.done} total={progress.total} elapsed={elapsed} />}
        {error && <div className="error">{error}</div>}

        {result && docs && (
          <div className="results">
            <div className={`summary ${totalIssues === 0 && !busy ? 'summary-ok' : 'summary-diff'}`}>
              {totalIssues === 0 && !busy
                ? <strong>✓ Отличий не найдено — документы идентичны</strong>
                : (
                  <strong>
                    Найдено отличий: {diffs.length} (графика: {graphicsCount}, текст: {textCount})
                    {removedCount > 0 && ` · листов удалено: ${removedCount}`}
                    {addedCount > 0 && ` · листов добавлено: ${addedCount}`}
                    {heavyCount > 0 && ` · сильно изменённых страниц: ${heavyCount}`}
                  </strong>
                )}
              <span className="summary-meta">
                страниц: {result.numPages1} и {result.numPages2}
                {result.elapsed > 0 && ` · ${result.elapsed} с`}
              </span>
            </div>

            {diffs.length > 0 && (
              <div className="diffnav">
                <button onClick={() => goTo(cur - 1)} aria-label="Предыдущее отличие">←</button>
                <span className="diffnav-pos">Отличие {Math.min(cur + 1, diffs.length)} из {diffs.length}</span>
                <button onClick={() => goTo(cur + 1)} aria-label="Следующее отличие">→</button>
                <span className="diffnav-info">
                  {diffs[Math.min(cur, diffs.length - 1)].type} · стр. {diffs[Math.min(cur, diffs.length - 1)].page + 1} · документ {diffs[Math.min(cur, diffs.length - 1)].side}
                </span>
                <button className="diffnav-show" onClick={() => goTo(cur)}>показать</button>
              </div>
            )}

            <div className="hint">
              Колесо мыши — масштаб, перетаскивание — перемещение по листу; оба документа двигаются синхронно.
            </div>

            {entries.map((entry) => {
              if (entry.kind === 'removed') {
                return (
                  <section className="page-pair" key={`removed-${entry.i}`}>
                    <h3>
                      Лист {entry.i + 1} документа 1
                      <span className="badge badge-diff">удалён во второй версии</span>
                    </h3>
                  </section>
                )
              }
              if (entry.kind === 'added') {
                return (
                  <section className="page-pair" key={`added-${entry.j}`}>
                    <h3>
                      Лист {entry.j + 1} документа 2
                      <span className="badge badge-diff">добавлен во второй версии</span>
                    </h3>
                  </section>
                )
              }
              return (
                <PagePair key={pairKeyOf(entry.p)} page={entry.p} docs={docs}
                          focus={focus} activeId={diffs[cur]?.id} renderW={renderW} />
              )
            })}
          </div>
        )}
      </main>
  )
}

export default function App() {
  const [user, setUser] = useState(undefined) //undefined — состояние ещё не известно
  const [kicked, setKicked] = useState(false)

  useEffect(() => {
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      const u = session?.user ?? null
      setUser(u)
      if (u) setKicked(false)
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  //единственная активная сессия: если в строке sessions с нашим uid появился
  //чужой ID сессии (вход с другого устройства) — разлогиниваемся.
  //Вход на этом устройстве пишет свой ID до подписки, поэтому свой же
  //логин нас не выбивает.
  useEffect(() => {
    if (!user) return
    if (!localStorage.getItem(sidKey(user.id))) claimSession(user.id)
    const channel = supabase
      .channel(`session-watch-${user.id}`)
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'sessions', filter: `uid=eq.${user.id}` },
          (payload) => {
            const remote = payload.new?.sid
            if (remote && remote !== localStorage.getItem(sidKey(user.id))) {
              localStorage.removeItem(sidKey(user.id))
              setKicked(true)
              supabase.auth.signOut()
            }
          })
      .subscribe()
    return () => { supabase.removeChannel(channel) }
  }, [user])

  return (
    <>
      <header className="topbar">
        <svg width="30" height="30" viewBox="0 0 16 16" aria-hidden="true">
          <rect x="1.5" y="1.5" width="13" height="13" fill="none" stroke="#e53935" strokeWidth="2" />
          <path d="M4.5 11 L8 5 L11.5 11" fill="none" stroke="currentColor" strokeWidth="1.4" />
        </svg>
        <div>
          <div className="brand">Сравнение чертежей</div>
          <div className="tagline">векторный анализ версий PDF-документов</div>
        </div>
        {user && (
          <div className="topbar-user">
            <span>{user.email}</span>
            <button type="button" className="logout-btn" onClick={() => supabase.auth.signOut()}>
              Выйти
            </button>
          </div>
        )}
      </header>
      {user === undefined && (
        <main className="login-wrap"><div className="spinner" /></main>
      )}
      {user === null && (
        <Login notice={kicked ? 'Выполнен вход с другого устройства — эта сессия завершена.' : ''} />
      )}
      {user && <Workspace user={user} />}
    </>
  )
}

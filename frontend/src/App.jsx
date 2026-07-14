import { useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const VIEW_W = 620          // ширина вьюпорта страницы, px
const RENDER_W = 2800       // разрешение рендера страницы, px (запас под зум)
const MAX_UPLOAD_MB = 4.4   // лимит тела запроса Vercel ~4.5 МБ

const mb = (bytes) => (bytes / 1024 / 1024).toFixed(2)

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

const STAGES = [
  [0, 'Загрузка файлов'],
  [2, 'Извлечение векторной геометрии'],
  [6, 'Сопоставление точек и поиск отличий'],
]

function Progress({ elapsed }) {
  const stage = STAGES.reduce((acc, [t, label]) => (elapsed >= t ? label : acc), STAGES[0][1])
  return (
    <div className="progress">
      <div className="spinner" />
      <div>
        <div className="stage">{stage}…</div>
        <div className="elapsed">{elapsed.toFixed(0)} с</div>
      </div>
    </div>
  )
}

function PageView({ pdfDoc, pageNumber, boxes, textBoxes, pageW, pageH, view, setView, label, activeId, side, pageIndex }) {
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
      const viewport = page.getViewport({ scale: RENDER_W / base.width })
      const canvas = canvasRef.current
      if (!canvas || cancelled) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      canvas.style.width = `${VIEW_W}px`
      canvas.style.height = 'auto'
      await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise
    })()
    return () => { cancelled = true }
  }, [pdfDoc, pageNumber])

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
            const id = `${side}-graphics-${pageIndex}-${i}`
            return <div key={id} className={`box box-graphics${id === activeId ? ' active' : ''}`} style={boxStyle(b)} />
          })}
          {textBoxes.map((b, i) => {
            const id = `${side}-text-${pageIndex}-${i}`
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

function PagePair({ page, docs, focus, activeId }) {
  const [view, setView] = useState({ z: 1, tx: 0, ty: 0 })
  const secRef = useRef(null)

  useEffect(() => {
    if (!focus || focus.pageIndex !== page.index) return
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

  return (
    <section className="page-pair" ref={secRef}>
      <h3>
        Страница {page.index + 1}
        {total === 0 && <span className="badge badge-ok">отличий не найдено</span>}
        {total > 0 && <span className="badge badge-diff">{total} отличий</span>}
        {page.lowConfidence && (
          <span className="badge badge-warn">
            низкая уверенность: страницы почти полностью различаются
          </span>
        )}
      </h3>
      {total > 0 && (
        <div className="pair-row">
          <PageView
            pdfDoc={docs.doc1} pageNumber={page.index + 1} side={1} pageIndex={page.index}
            boxes={page.boxes1} textBoxes={page.textBoxes1}
            pageW={page.width1} pageH={page.height1}
            view={view} setView={setView} label="Документ 1" activeId={activeId}
          />
          <PageView
            pdfDoc={docs.doc2} pageNumber={page.index + 1} side={2} pageIndex={page.index}
            boxes={page.boxes2} textBoxes={page.textBoxes2}
            pageW={page.width2} pageH={page.height2}
            view={view} setView={setView} label="Документ 2" activeId={activeId}
          />
        </div>
      )}
    </section>
  )
}

export default function App() {
  const [file1, setFile1] = useState(null)
  const [file2, setFile2] = useState(null)
  const [docs, setDocs] = useState(null)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
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

  const tooBig = (file1?.size || 0) + (file2?.size || 0) > MAX_UPLOAD_MB * 1024 * 1024

  const diffs = useMemo(() => {
    if (!result) return []
    const list = []
    for (const p of result.pages) {
      p.boxes1.forEach((box, i) => list.push({ id: `1-graphics-${p.index}-${i}`, page: p.index, side: 1, type: 'графика', box }))
      p.boxes2.forEach((box, i) => list.push({ id: `2-graphics-${p.index}-${i}`, page: p.index, side: 2, type: 'графика', box }))
      p.textBoxes1.forEach((box, i) => list.push({ id: `1-text-${p.index}-${i}`, page: p.index, side: 1, type: 'текст', box }))
      p.textBoxes2.forEach((box, i) => list.push({ id: `2-text-${p.index}-${i}`, page: p.index, side: 2, type: 'текст', box }))
    }
    return list
  }, [result])

  const graphicsCount = diffs.filter((d) => d.type === 'графика').length
  const textCount = diffs.length - graphicsCount

  function goTo(idx) {
    const i = (idx + diffs.length) % diffs.length
    setCur(i)
    const d = diffs[i]
    setFocus({ pageIndex: d.page, box: d.box, seq: Date.now() })
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
    try {
      const fd = new FormData()
      fd.append('file1', file1)
      fd.append('file2', file2)
      const resp = await fetch('/api/compare', { method: 'POST', body: fd })
      if (resp.status === 413) throw new Error('Файлы превышают лимит загрузки (~4.5 МБ суммарно). Попробуйте отдельные страницы.')
      if (resp.status === 504) throw new Error('Сервер не успел обработать документы за отведённое время. Попробуйте сравнить меньше страниц за раз.')
      if (!resp.ok) {
        let msg = `Ошибка сервера (${resp.status})`
        try { msg = (await resp.json()).error || msg } catch { /* not json */ }
        throw new Error(msg)
      }
      const data = await resp.json()
      const [buf1, buf2] = await Promise.all([file1.arrayBuffer(), file2.arrayBuffer()])
      const [doc1, doc2] = await Promise.all([
        pdfjsLib.getDocument({ data: buf1 }).promise,
        pdfjsLib.getDocument({ data: buf2 }).promise,
      ])
      setDocs({ doc1, doc2 })
      setResult(data)
    } catch (err) {
      if (err instanceof TypeError) {
        setError('Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.')
      } else {
        setError(String(err.message || err))
      }
    } finally {
      setBusy(false)
    }
  }

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
      </header>

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
                Суммарный размер больше {MAX_UPLOAD_MB} МБ — загрузка не пройдёт.
                Разбейте документы на страницы.
              </div>
            )}
          </div>
        </form>

        {busy && <Progress elapsed={elapsed} />}
        {error && <div className="error">{error}</div>}

        {result && docs && (
          <div className="results">
            <div className={`summary ${diffs.length === 0 ? 'summary-ok' : 'summary-diff'}`}>
              {diffs.length === 0
                ? <strong>✓ Отличий не найдено — документы идентичны</strong>
                : <strong>Найдено отличий: {diffs.length} (графика: {graphicsCount}, текст: {textCount})</strong>}
              <span className="summary-meta">
                страниц: {result.numPages1} и {result.numPages2}
                {result.numPages1 !== result.numPages2 && ' — сравнены первые совпадающие'}
                {' · '}{result.elapsed} с
              </span>
            </div>

            {diffs.length > 0 && (
              <div className="diffnav">
                <button onClick={() => goTo(cur - 1)} aria-label="Предыдущее отличие">←</button>
                <span className="diffnav-pos">Отличие {cur + 1} из {diffs.length}</span>
                <button onClick={() => goTo(cur + 1)} aria-label="Следующее отличие">→</button>
                <span className="diffnav-info">
                  {diffs[cur].type} · стр. {diffs[cur].page + 1} · документ {diffs[cur].side}
                </span>
                <button className="diffnav-show" onClick={() => goTo(cur)}>показать</button>
              </div>
            )}

            <div className="hint">
              Колесо мыши — масштаб, перетаскивание — перемещение по листу; оба документа двигаются синхронно.
            </div>

            {result.pages.map((p) => (
              <PagePair key={p.index} page={p} docs={docs} focus={focus}
                        activeId={diffs[cur]?.id} />
            ))}
          </div>
        )}
      </main>
    </>
  )
}

import { useEffect, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const PAGE_WIDTH = 620

function PageCanvas({ pdfDoc, pageNumber, boxes, textBoxes, label }) {
  const canvasRef = useRef(null)
  const [scale, setScale] = useState(0)

  useEffect(() => {
    if (!pdfDoc) return
    let cancelled = false
    ;(async () => {
      const page = await pdfDoc.getPage(pageNumber)
      const base = page.getViewport({ scale: 1 })
      const s = PAGE_WIDTH / base.width
      const viewport = page.getViewport({ scale: s })
      const canvas = canvasRef.current
      if (!canvas || cancelled) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise
      if (!cancelled) setScale(s)
    })()
    return () => { cancelled = true }
  }, [pdfDoc, pageNumber])

  const rect = (b) => ({
    left: b[0] * scale,
    top: b[1] * scale,
    width: (b[2] - b[0]) * scale,
    height: (b[3] - b[1]) * scale,
  })

  return (
    <div className="page-view">
      <div className="page-label">{label}</div>
      <div className="canvas-wrap">
        <canvas ref={canvasRef} />
        {scale > 0 && boxes.map((b, i) => (
          <div key={`g${i}`} className="box box-graphics" style={rect(b)} />
        ))}
        {scale > 0 && textBoxes.map((b, i) => (
          <div key={`t${i}`} className="box box-text" style={rect(b)} />
        ))}
      </div>
    </div>
  )
}

function PagePair({ page, docs }) {
  const total =
    page.boxes1.length + page.boxes2.length +
    page.textBoxes1.length + page.textBoxes2.length
  return (
    <section className="page-pair">
      <h3>
        Страница {page.index + 1}
        {total === 0 && <span className="badge badge-ok">отличий не найдено</span>}
        {total > 0 && <span className="badge badge-diff">{total} отличий</span>}
        {page.lowConfidence && (
          <span className="badge badge-warn">
            низкая уверенность: векторные представления страниц сильно расходятся
          </span>
        )}
      </h3>
      {total > 0 && (
        <div className="pair-row">
          <PageCanvas
            pdfDoc={docs.doc1} pageNumber={page.index + 1}
            boxes={page.boxes1} textBoxes={page.textBoxes1} label="Документ 1"
          />
          <PageCanvas
            pdfDoc={docs.doc2} pageNumber={page.index + 1}
            boxes={page.boxes2} textBoxes={page.textBoxes2} label="Документ 2"
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
  const [error, setError] = useState('')

  async function handleCompare(e) {
    e.preventDefault()
    if (!file1 || !file2) {
      setError('Выберите оба PDF-файла')
      return
    }
    setBusy(true)
    setError('')
    setResult(null)
    setDocs(null)
    try {
      const fd = new FormData()
      fd.append('file1', file1)
      fd.append('file2', file2)
      const resp = await fetch('/api/compare', { method: 'POST', body: fd })
      if (!resp.ok) {
        throw new Error(`Ошибка API (${resp.status}): ${await resp.text()}`)
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
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main>
      <h1>Сравнение чертежей</h1>
      <p className="subtitle">
        Загрузите две версии PDF-документа — отличия в графике будут обведены
        красным, отличия в тексте — оранжевым.
      </p>

      <form onSubmit={handleCompare} className="upload-form">
        <label className="file-input">
          <span>Документ 1</span>
          <input type="file" accept=".pdf"
                 onChange={(e) => setFile1(e.target.files[0] || null)} />
          <span className="file-name">{file1 ? file1.name : 'файл не выбран'}</span>
        </label>
        <label className="file-input">
          <span>Документ 2</span>
          <input type="file" accept=".pdf"
                 onChange={(e) => setFile2(e.target.files[0] || null)} />
          <span className="file-name">{file2 ? file2.name : 'файл не выбран'}</span>
        </label>
        <button type="submit" disabled={busy}>
          {busy ? 'Сравниваю…' : 'Сравнить'}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {result && docs && (
        <div className="results">
          <div className="summary">
            Страниц: {result.numPages1} и {result.numPages2}
            {result.numPages1 !== result.numPages2 &&
              ' (количество страниц различается — сравнены первые совпадающие)'}
            {' · '}время сравнения: {result.elapsed} с
          </div>
          {result.pages.map((p) => (
            <PagePair key={p.index} page={p} docs={docs} />
          ))}
        </div>
      )}
    </main>
  )
}

//формирование DOCX-отчёта согласования отличий
//вынесено из компонента, чтобы генерацию можно было проверять отдельно

//ширины колонок в твипах; сумма ~9638 = полоса набора A4 с полями 2 см
const COLS = [700, 1700, 1800, 1800, 3638]

export async function buildReportDoc(meta, items) {
  const { Document, Paragraph, TextRun, Table, TableRow, TableCell,
          WidthType, TableLayoutType, ShadingType } = await import('docx')

  const cell = (text, col, opts = {}) => new TableCell({
    width: { size: COLS[col], type: WidthType.DXA },
    shading: opts.shade ? { type: ShadingType.CLEAR, fill: 'E8E8E8' } : undefined,
    margins: { top: 80, bottom: 80, left: 110, right: 110 },
    children: [new Paragraph({
      children: [new TextRun({ text: String(text ?? ''), bold: !!opts.bold, size: 20 })],
    })],
  })

  const headerRow = new TableRow({
    tableHeader: true,
    children: ['№', 'Лист', 'Изменения', 'Статус', 'Комментарий']
      .map((t, c) => cell(t, c, { bold: true, shade: true })),
  })
  const rows = items.map((it, idx) => new TableRow({
    children: [
      cell(idx + 1, 0),
      cell(it.page, 1),
      cell(it.type, 2),
      cell(it.status, 3),
      cell(it.comment, 4),
    ],
  }))

  const line = (text, opts = {}) => new Paragraph({
    spacing: { after: 120 },
    children: [new TextRun({ text, bold: !!opts.bold, size: opts.size || 22 })],
  })

  return new Document({
    styles: {
      default: {
        document: { run: { font: 'Calibri', size: 22 } },
      },
    },
    sections: [{
      children: [
        line('Отчёт о согласовании изменений', { bold: true, size: 32 }),
        line(`Документ 1: ${meta.file1}`),
        line(`Документ 2: ${meta.file2}`),
        line(`Дата: ${meta.date}`),
        line(`Листов с изменениями: ${meta.total} · Согласовано: ${meta.approved} · С замечаниями: ${meta.commented}`),
        new Table({
          layout: TableLayoutType.FIXED,
          width: { size: COLS.reduce((a, b) => a + b, 0), type: WidthType.DXA },
          columnWidths: COLS,
          rows: [headerRow, ...rows],
        }),
      ],
    }],
  })
}

export async function downloadReport(meta, items) {
  const { Packer } = await import('docx')
  const doc = await buildReportDoc(meta, items)
  const blob = await Packer.toBlob(doc)
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `otchet-sravneniya-${meta.date}.docx`
  a.click()
  URL.revokeObjectURL(a.href)
}

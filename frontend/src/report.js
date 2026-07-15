//формирование DOCX-отчёта согласования отличий
//вынесено из компонента, чтобы генерацию можно было проверять отдельно

export async function buildReportDoc(meta, items) {
  const { Document, Paragraph, TextRun, Table, TableRow, TableCell, WidthType } =
    await import('docx')

  const cell = (text, opts = {}) => new TableCell({
    width: opts.width ? { size: opts.width, type: WidthType.PERCENTAGE } : undefined,
    children: [new Paragraph({
      children: [new TextRun({ text: String(text ?? ''), bold: !!opts.bold })],
    })],
  })

  const header = new TableRow({
    tableHeader: true,
    children: [
      cell('№', { bold: true, width: 6 }),
      cell('Страница', { bold: true, width: 16 }),
      cell('Тип', { bold: true, width: 16 }),
      cell('Статус', { bold: true, width: 18 }),
      cell('Комментарий', { bold: true, width: 44 }),
    ],
  })
  const rows = items.map((it, idx) => new TableRow({
    children: [cell(idx + 1), cell(it.page), cell(it.type), cell(it.status), cell(it.comment)],
  }))

  const line = (text, opts = {}) => new Paragraph({
    children: [new TextRun({ text, bold: !!opts.bold, size: opts.size })],
  })

  return new Document({
    sections: [{
      children: [
        line('Отчёт о согласовании изменений', { bold: true, size: 32 }),
        line(''),
        line(`Документ 1: ${meta.file1}`),
        line(`Документ 2: ${meta.file2}`),
        line(`Дата: ${meta.date}`),
        line(`Отличий: ${meta.total} · Согласовано: ${meta.approved} · С замечаниями: ${meta.commented}`),
        line(''),
        new Table({ width: { size: 100, type: WidthType.PERCENTAGE }, rows: [header, ...rows] }),
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

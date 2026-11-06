// Usage: node builder.js spec.json out.docx header.json
// spec.type = "resume" | "cover". header.json (written by build_doc.py from profile.yaml):
//   {name, location, phone, email, links:[{label,url}], education:[{line, date, detail?}]}
const fs = require('fs');
const { Document, Packer, Paragraph, TextRun, ExternalHyperlink, AlignmentType, LevelFormat, BorderStyle, Table, TableRow, TableCell, WidthType } = require('docx');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = process.argv[3];
const H = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const FONT = 'Calibri', SZ = 21, RIGHT = 12240 - 2 * 864;
const run = (t, o = {}) => new TextRun({ text: t, font: FONT, size: SZ, ...o });
const link = (l, u) => new ExternalHyperlink({ link: u, children: [run(l, { color: '1155CC', underline: {} })] });
const section = (t) => new Paragraph({ spacing: { before: 140, after: 60 }, border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: '444444', space: 1 } }, children: [run(t.toUpperCase(), { bold: true, size: 22 })] });
const NB = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
const NOB = { top: NB, bottom: NB, left: NB, right: NB, insideHorizontal: NB, insideVertical: NB };
const LW = Math.round(RIGHT * 0.72), RW = RIGHT - LW;
// Left text / right-aligned text on one line, as a borderless 2-column table (renders the same in Word, Google Docs and Drive preview)
const twoCol = (l, r, lo = {}, ro = {}, before = 0) => new Table({
  width: { size: RIGHT, type: WidthType.DXA }, columnWidths: [LW, RW], borders: NOB,
  rows: [new TableRow({ children: [
    new TableCell({ width: { size: LW, type: WidthType.DXA }, borders: NOB, margins: { top: 0, bottom: 0, left: 0, right: 0 }, children: [new Paragraph({ spacing: { before, after: 0 }, children: [run(l, lo)] })] }),
    new TableCell({ width: { size: RW, type: WidthType.DXA }, borders: NOB, margins: { top: 0, bottom: 0, left: 0, right: 0 }, children: [new Paragraph({ alignment: AlignmentType.RIGHT, spacing: { before, after: 0 }, children: [run(r, ro)] })] }),
  ] })] });
const bullet = (t) => new Paragraph({ numbering: { reference: 'b', level: 0 }, spacing: { after: 20 }, children: [run(t)] });
const para = (c, o = {}) => new Paragraph({ spacing: { after: 20 }, ...o, children: c });
const contact = [H.location, H.phone].filter(Boolean).join('  •  ');
const header = (tagline) => [
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 20 }, children: [run(H.name, { bold: true, size: 34 })] }),
  ...(tagline ? [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 20 }, children: [run(tagline)] })] : []),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 }, children: [run(contact + (H.email ? '  •  ' : '')), ...(H.email ? [link(H.email, 'mailto:' + H.email)] : [])] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 }, children: (H.links || []).flatMap((l, i) => [...(i ? [run('  •  ')] : []), link(l.label, l.url)]) }),
];
const education = () => (H.education || []).flatMap((e, i) => [twoCol(e.line, e.date || '', { bold: true }, {}, i ? 40 : 0), ...(e.detail ? [para([run(e.detail)])] : [])]);
const job = (j) => [twoCol(j.org, j.loc, { bold: true }, {}, 80), twoCol(j.title, j.dates, { italics: true }, { italics: true }), ...(j.bullets || []).map(bullet)];
let children;
if (spec.type === 'cover') {
  children = [...header(null), para([run(spec.date)], { spacing: { before: 240, after: 200 } }),
    ...(spec.recipient || []).map(l => para([run(l)])),
    para([run(spec.salutation || 'Dear Hiring Team,')], { spacing: { before: 200, after: 160 } }),
    ...spec.paragraphs.map(p => para([run(p)], { spacing: { after: 160 } })),
    para([run('Sincerely,')], { spacing: { before: 80, after: 20 } }), para([run(H.name)])];
} else {
  children = [...header(spec.tagline), section('Summary'), para([run(spec.summary)]), section('Education'),
    ...education(),
    section('Experience'), ...spec.jobs.flatMap(job), section('Technical Skills'),
    ...spec.skills.map(([k, v]) => para([run(k + ': ', { bold: true }), run(v)]))];
}
const doc = new Document({ numbering: { config: [{ reference: 'b', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 300, hanging: 220 } } } }] }] },
  sections: [{ properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 720, bottom: 720, left: 864, right: 864 } } }, children }] });
Packer.toBuffer(doc).then(b => { fs.writeFileSync(out, b); console.log('wrote', out); });

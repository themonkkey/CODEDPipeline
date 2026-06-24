export const meta = {
  name: 'pipeline-quality-grade',
  description: 'Grade CODEDPipeline output quality from objective corpus metrics via a judge panel + adversarial check + synthesis',
  phases: [
    { title: 'Grade', detail: 'one judge per quality dimension scores 0-5 from the metrics' },
    { title: 'Challenge', detail: 'adversarial reviewers re-check each score against raw metrics' },
    { title: 'Synthesize', detail: 'combine into one weighted overall score + letter grade' },
  ],
}

const corpus = args  // aggregated corpus scorecard (JSON object)
const METRICS = JSON.stringify(corpus, null, 1)

const BASELINE = `
TWO PRIOR AUDIT POINTS (so you can judge the trajectory):
A) ORIGINAL baseline (before any fixes): analyst-usability C-:
   cell content 3.2/5, columns ~2.7/5, headings ~2/5, sub-headings 1.0/5
   (core failure: section banners + multi-level headers lost), numbers shipped
   as strings (not analysis-ready), census workbooks ~928/1126 ghost/empty
   sheets, titles truncated at 10 words.
B) MID-POINT grade (after the first 5 fixes): overall C+ 64.2/100. Robustness
   was the laggard at 3.6/5 (~15% of tables leaking corrupt Hindi).

EVERYTHING SHIPPED (all guarded GREEN, 35 guard groups A–AJ):
 1 numeric normalization (strings -> int/float, unicode-minus, blanks for missing)
 2 multi-level header merge (composite names like average_mpce_rural_2022_23)
 3 section-row lift (in-table banners -> a forward-filled 'category' column)
 4 navigable workbook (Contents/TOC first, full titles to 18 words, readable tabs)
 5 ghost-sheet suppression (index-legend-only fragments dropped)
 6 column-name dedupe + phantom numeric value-column naming (no col_N dup leaks)
 7 thin sub-header absorb + descriptive-title capture + continuation-title inheritance
 8 chapter/section heading carry-forward across pages
 9 OCR RECOVERY (NEW): font-corrupt Kruti-Dev tables (kind=kruti) are re-read by
   rendering glyphs at 300dpi + tesseract(hin+eng) + Devanagari->Latin translit,
   bucketed back into the Camelot cell grid. Unicode Devanagari (kind=deva) stays
   on the normal translation path. Tables still corrupt after the OCR attempt are
   QUARANTINED (reason=garbled_source) into failed_tables.csv, never shipped as clean.

KEY METRICS TO USE (current run, this corpus, big PDFs page-capped at 40pp):
 - mean_numeric_readiness = the HONEST numeric metric: among columns that are
   INTENDED numeric, the fraction of cells actually typed as numbers. Use THIS
   over mean_numeric_value_frac (which is diluted by legitimate text dimension
   columns like state/indicator and understates readiness).
 - ocr_recovered_total = corrupt tables rescued by OCR this run.
 - garbled_quarantined_total = unrecoverable corrupt tables transparently quarantined.
 - tables_with_deva_frac = tables still carrying Devanagari row-label text. NOTE:
   these are kind=deva (translatable, numerically clean: readiness ~1.0), NOT the
   corrupt Kruti soup — the leakage is untranslated row LABELS, not bad data.
`

const DIM_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    score_0_5: { type: 'number' },
    letter: { type: 'string' },
    evidence: { type: 'array', items: { type: 'string' }, description: 'specific metric values cited' },
    strengths: { type: 'array', items: { type: 'string' } },
    weaknesses: { type: 'array', items: { type: 'string' } },
    before_after: { type: 'string', description: 'how this dimension changed vs the C- baseline' },
  },
  required: ['dimension', 'score_0_5', 'letter', 'evidence', 'weaknesses', 'before_after'],
}

const CHALLENGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    agree: { type: 'boolean' },
    adjusted_score_0_5: { type: 'number' },
    reason: { type: 'string' },
  },
  required: ['dimension', 'agree', 'adjusted_score_0_5', 'reason'],
}

const SYNTH_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    overall_score_0_100: { type: 'number' },
    letter_grade: { type: 'string' },
    one_line_verdict: { type: 'string' },
    weighted_breakdown: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          dimension: { type: 'string' },
          score_0_5: { type: 'number' },
          weight_pct: { type: 'number' },
        },
        required: ['dimension', 'score_0_5', 'weight_pct'],
      },
    },
    before_after: { type: 'string' },
    top_remaining_gaps: { type: 'array', items: { type: 'string' } },
    analyst_ready_verdict: { type: 'string' },
  },
  required: ['overall_score_0_100', 'letter_grade', 'one_line_verdict', 'weighted_breakdown',
             'before_after', 'top_remaining_gaps', 'analyst_ready_verdict'],
}

const DIMENSIONS = [
  { key: 'Column integrity',
    focus: 'col_n phantom columns and duplicate column names. mean_col_n_frac (lower=better), tables_zero_coln_frac (higher=better), tables_with_dup_cols_frac.' },
  { key: 'Table titles / headings',
    focus: 'are tables given real, complete titles. named_frac, plus de-truncation (titles up to 18 words now).' },
  { key: 'Sub-headings (multi-level + sections)',
    focus: 'THE prior core failure (1.0/5). composite_frac (multi-level headers merged into composite names) and category_frac (in-table section banners lifted into a category column).' },
  { key: 'Cell content / numeric readiness',
    focus: 'can an analyst aggregate without cleaning. PRIMARY metric = mean_numeric_readiness (honest: typed-fraction over intended-numeric columns only). tables_with_numeric_cols_frac (numbers typed int/float not strings). mean_numeric_value_frac is the OLD diluted metric — weight it lightly.' },
  { key: 'Structural integrity',
    focus: 'orphan label-less rows and shape correctness. tables_with_orphans_frac, total orphans, tables_with_dup_cols_frac (now ~0 after dedupe).' },
  { key: 'Robustness / noise control',
    focus: 'failure modes and noise handling. THIS was the laggard at 3.6/5 — judge whether OCR recovery + transparent quarantine fixed it. failed_reasons breakdown (front_matter/mostly_empty are CORRECT rejects of non-data, not failures), ocr_recovered_total (corrupt tables rescued = good), garbled_quarantined_total (unrecoverable corrupt tables quarantined not shipped = good, honest), tables_with_deva_frac (translatable kind=deva row LABELS, data underneath is clean — mild leakage not corruption), pdfs_errored (0 = no crashes on the whole corpus).' },
]

phase('Grade')
const graded = await pipeline(
  DIMENSIONS,
  (d) => agent(
    `You are a data-quality auditor grading ONE dimension of a PDF-table extraction pipeline used to feed downstream data analysis.\n` +
    `Dimension: "${d.key}". What it measures: ${d.focus}\n\n` +
    `Grade ONLY from these objective corpus metrics (measured on the current pipeline over the test corpus; big PDFs page-capped):\n${METRICS}\n` +
    `\n${BASELINE}\n` +
    `Score this dimension 0-5 (5=excellent, analyst needs no rework; 0=unusable). Give a letter grade, cite the exact metric values as evidence, list strengths and weaknesses, and state how it changed vs the C- baseline. Be rigorous and skeptical — do not inflate.`,
    { label: `grade:${d.key}`, phase: 'Grade', schema: DIM_SCHEMA }
  ),
  // Challenge stage runs per-dimension as soon as its grade lands
  (g, d) => agent(
    `Adversarially re-check this dimension grade against the raw metrics. Default to skepticism: if the score looks too generous OR too harsh given the numbers, adjust it.\n\n` +
    `Dimension: ${d.key}\nProposed grade: ${JSON.stringify(g)}\n\nMetrics:\n${METRICS}\n` +
    `Return whether you agree, the score you'd defend (0-5), and why in one or two sentences.`,
    { label: `challenge:${d.key}`, phase: 'Challenge', schema: CHALLENGE_SCHEMA }
  ).then((c) => ({ ...g, challenge: c,
    final_score: c.agree ? g.score_0_5 : (g.score_0_5 + c.adjusted_score_0_5) / 2 }))
)

const dims = graded.filter(Boolean)
log(`graded ${dims.length} dimensions; mean final ${(dims.reduce((s, x) => s + x.final_score, 0) / dims.length).toFixed(2)}/5`)

phase('Synthesize')
const synthesis = await agent(
  `You are the lead reviewer. Combine these independently-graded, adversarially-checked dimensions into ONE overall quality score for the pipeline's output.\n\n` +
  `Per-dimension results (use final_score):\n${JSON.stringify(dims.map(d => ({
    dimension: d.dimension, judge_score: d.score_0_5, challenged_score: d.final_score,
    weaknesses: d.weaknesses, before_after: d.before_after })), null, 1)}\n\n` +
  `Corpus metrics:\n${METRICS}\n${BASELINE}\n` +
  `Produce: overall_score_0_100, a letter_grade, a one-line verdict, a weighted_breakdown ` +
  `(assign weights reflecting analyst impact — cell/numeric readiness and column integrity matter most, ` +
  `then sub-headings, then titles/structure/robustness; weights sum to 100), the before/after vs C-, ` +
  `the top remaining gaps, and a blunt analyst-ready verdict (can a data analyst use these spreadsheets without major rework?).`,
  { label: 'synthesize', phase: 'Synthesize', schema: SYNTH_SCHEMA, effort: 'high' }
)

return { synthesis, dimensions: dims.map(d => ({
  dimension: d.dimension, judge_score: d.score_0_5, final_score: d.final_score,
  letter: d.letter, evidence: d.evidence, weaknesses: d.weaknesses })) }

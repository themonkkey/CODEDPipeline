export const meta = {
  name: 'pipeline-improve-to-4.5',
  description: 'Design a concrete, codebase-grounded roadmap to push every quality dimension to 4.5+/5',
  phases: [
    { title: 'Design', detail: 'one engineer per dimension reads the real code and proposes the fix to reach 4.5' },
    { title: 'Challenge', detail: 'adversary checks feasibility, regression risk, and whether 4.5 is reachable rule-based or needs ML' },
    { title: 'Synthesize', detail: 'sequence into one prioritized roadmap with effort + expected new score' },
  ],
}

const ROOT = '/Users/thesinghaa/Downloads/coded-stat-engine 2'

const CONTEXT = `
PROJECT: CODEDPipeline — Camelot+pdfplumber PDF table extractor. Pipeline order:
extract_tables -> clean_dataframe -> split_panels -> reassemble_wrapped_rows ->
translate_dataframe -> detect_header_rows -> extract_table_name -> apply_headers ->
clean_headers -> merge_continuation_values -> lift_section_rows ->
normalize_numeric_columns -> validate_table -> stitch_tables -> excel_exporter.
Repo root: ${ROOT}. Use Read/Grep/Bash (.venv/bin/python) on real files; cite real
function names + line numbers. 22 regression guards (A-V) in
backend/tools/regression_guards.py must STAY GREEN — every change ships with a guard.
Measured corpus: 301 tables / 28 PDFs. Current overall C+ (64.2/100).
A measurement harness exists: backend/tools/measure_quality.py + measure_all.py +
aggregate_quality.py (writes /tmp/m/_corpus.json) — re-runnable to prove deltas.
`

const DIM_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    current_score: { type: 'number' },
    root_cause: { type: 'string', description: 'the real reason it is stuck, grounded in specific code' },
    changes: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          file: { type: 'string' },
          what: { type: 'string' },
          how: { type: 'string', description: 'concrete approach incl function/line' },
          new_guard: { type: 'string' },
          effort: { type: 'string', enum: ['S', 'M', 'L', 'XL'] },
          regression_risk: { type: 'string', enum: ['low', 'medium', 'high'] },
        },
        required: ['file', 'what', 'how', 'new_guard', 'effort', 'regression_risk'],
      },
    },
    needs_ml_or_docling: { type: 'boolean', description: 'true if 4.5 is NOT reachable with rule-based changes alone' },
    expected_score_after: { type: 'number' },
    honest_ceiling: { type: 'string', description: 'realistic max for this dimension and why' },
  },
  required: ['dimension', 'current_score', 'root_cause', 'changes', 'needs_ml_or_docling',
             'expected_score_after', 'honest_ceiling'],
}

const CHALLENGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    plan_is_sound: { type: 'boolean' },
    will_regress_guards: { type: 'string', description: 'which guards/tables are at risk and why' },
    realistic_score_after: { type: 'number' },
    cheapest_high_impact_change: { type: 'string' },
    verdict: { type: 'string' },
  },
  required: ['dimension', 'plan_is_sound', 'will_regress_guards', 'realistic_score_after',
             'cheapest_high_impact_change', 'verdict'],
}

const SYNTH_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    reachable_summary: { type: 'string', description: 'can 4.5+ be hit on almost all? blunt answer' },
    sequenced_roadmap: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          step: { type: 'number' },
          title: { type: 'string' },
          dimensions_moved: { type: 'array', items: { type: 'string' } },
          effort: { type: 'string' },
          expected_overall_delta: { type: 'string' },
          why_this_order: { type: 'string' },
        },
        required: ['step', 'title', 'dimensions_moved', 'effort', 'expected_overall_delta', 'why_this_order'],
      },
    },
    rule_based_ceiling_overall: { type: 'number', description: 'best overall /100 with rule-based work only' },
    ml_gated_dimensions: { type: 'array', items: { type: 'string' } },
    projected_overall_after_all: { type: 'number' },
    bottom_line: { type: 'string' },
  },
  required: ['reachable_summary', 'sequenced_roadmap', 'rule_based_ceiling_overall',
             'ml_gated_dimensions', 'projected_overall_after_all', 'bottom_line'],
}

const DIMENSIONS = [
  { key: 'Column integrity', cur: 2.75, weight: 24,
    gap: 'dup column names in 35.5% of tables; ~49% of tables carry >=1 phantom col_N column; mean col_n_frac 0.251. ghosts_dropped_total=0 means nothing prunes phantoms.',
    files: 'backend/app/cleaning/header_builder.py (apply_headers: col_N fallback ~line 628, ghost-column drop ~674-691, _absorb_subheader_rows dedup ~line 290), backend/app/cleaning/header_postprocessor.py',
    hyp: 'Add a final column-name dedup pass that disambiguates duplicates with the differentiating header token or panel/positional suffix; classify phantom col_N as empty-separator (drop) vs real-data-lost-header (rename value_N or recover from content); make naming panel-aware so repeated header blocks dont collide.' },
  { key: 'Cell content / numeric readiness', cur: 3.7, weight: 28,
    gap: 'mean numeric_value_frac 0.623; the metric counts ALL value columns incl legitimately-text dimension columns, so part is measurement; real ceilings on plfs_calendar (0.333), darpg_jan (0.632) from footnotes/NA/dash/ranges past the 80% cast threshold.',
    files: 'backend/app/cleaning/numeric_normalizer.py, backend/tools/measure_quality.py (metric definition)',
    hyp: 'Refine the readiness metric to score only columns that SHOULD be numeric; broaden _to_number/_column_is_castable for more real formats; decide whether to lower the 0.8 threshold; separate genuine text columns from poisoned numeric ones.' },
  { key: 'Sub-headings (multi-level + sections)', cur: 2.8, weight: 16,
    gap: 'composite_frac 0.093, category_frac 0.173 — both thin; capped because detect_header_rows under-counts header rows so multi-level headers leak; no false-positive instrumentation for the lifted category column.',
    files: 'backend/app/cleaning/header_detector.py (detect_header_rows), backend/app/cleaning/header_builder.py (_absorb_subheader_rows, _is_subheader_row), backend/app/cleaning/section_lifter.py',
    hyp: 'Strengthen header-row DETECTION so multi-level headers are caught at source not just absorbed; safely relax absorb/section thresholds with precision guards; add a category-boundary correctness check.' },
  { key: 'Table titles / headings', cur: 2.9, weight: 11,
    gap: 'named_frac 0.528 — ~47% of tables have no title at all.',
    files: 'backend/app/standardization/table_name_extractor.py, backend/app/extract/table_extractor.py (where caption comes from)',
    hyp: 'Capture the title line from positional text just above the table bbox via pdfplumber (most tables have a heading camelot does not return); add section-heading fallback from page context; fix prose-caption-as-title.' },
  { key: 'Robustness / noise control', cur: 3.25, weight: 8,
    gap: '14.3% of tables leak Devanagari; economic_survey_hindi is confirmed mojibake (16 corrupted tables); ghost-suppression counter reads 0 in the sample (unverified positively).',
    files: 'backend/app/translation/hindi_translator.py, backend/app/translation/kruti_dev.py, backend/app/validation/table_validator.py',
    hyp: 'Detect mojibake/garbled-glyph tables and quarantine or flag them (do not ship corrupt as clean); consider an OCR fallback for Devanagari; positively verify ghost suppression on a deep census slice.' },
  { key: 'Structural integrity', cur: 3.75, weight: 13,
    gap: 'rides mostly on dup columns (35.5%); orphans already near-zero (0.02). Panel split coverage and shape correctness on wide/side-by-side tables.',
    files: 'backend/app/cleaning/panel_splitter.py, backend/app/cleaning/wrapped_row_reassembler.py, backend/app/cleaning/header_builder.py',
    hyp: 'Largely inherits the column-dedup fix; plus widen side-by-side / panel split coverage and confirm no shape regressions.' },
]

phase('Design')
const designed = await pipeline(
  DIMENSIONS,
  (d) => agent(
    `${CONTEXT}\n\nYou are the engineer owning ONE quality dimension. Get it from ${d.cur}/5 to 4.5+/5.\n` +
    `Dimension: "${d.key}" (weight ${d.weight}% of overall grade).\n` +
    `Measured gap: ${d.gap}\n` +
    `Start in these files: ${d.files}\n` +
    `Working hypothesis to verify/refine against the REAL code: ${d.hyp}\n\n` +
    `READ the actual code before proposing anything. Produce concrete changes (file + what + how with function/line + the new regression guard each needs + effort S/M/L/XL + regression risk). ` +
    `Say honestly whether 4.5 needs ML/Docling or is reachable with rule-based work, the expected score after, and the honest ceiling. Be specific and grounded — no hand-waving.`,
    { label: `design:${d.key}`, phase: 'Design', schema: DIM_SCHEMA, effort: 'high' }
  ),
  (plan, d) => agent(
    `${CONTEXT}\n\nAdversarially review this improvement plan for "${d.key}". Be skeptical.\n\n` +
    `Plan: ${JSON.stringify(plan)}\n\n` +
    `Check: (1) will any change risk the 22 guards (A-V) or known-good tables (NFHS/PLFS/DARPG/RBI/HCES)? name them. ` +
    `(2) is the claimed effort honest? (3) is expected_score_after realistic or inflated? ` +
    `(4) what is the single cheapest high-impact change here? Read code if needed to verify.`,
    { label: `challenge:${d.key}`, phase: 'Challenge', schema: CHALLENGE_SCHEMA, effort: 'high' }
  ).then((c) => ({ ...plan, challenge: c }))
)

const plans = designed.filter(Boolean)
log(`designed ${plans.length} dimension plans; ${plans.filter(p => p.needs_ml_or_docling).length} flagged ML-gated`)

phase('Synthesize')
const roadmap = await agent(
  `${CONTEXT}\n\nYou are the tech lead. The goal: 4.5+/5 on ALMOST ALL six dimensions.\n` +
  `Per-dimension plans (with adversarial challenge):\n${JSON.stringify(plans.map(p => ({
    dimension: p.dimension, current: p.current_score, root_cause: p.root_cause,
    changes: p.changes, needs_ml: p.needs_ml_or_docling, expected_after: p.expected_score_after,
    ceiling: p.honest_ceiling, challenge: p.challenge })), null, 1)}\n\n` +
  `Weights: numeric 28, column 24, sub-headings 16, structural 13, titles 11, robustness 8.\n` +
  `Produce a SEQUENCED roadmap ordered by (impact = weight x gap) and dependency (e.g. column-dedup unblocks structural). ` +
  `For each step: title, which dimensions it moves, effort, expected overall delta, why this order. ` +
  `State bluntly: can 4.5+ be hit on almost all with rule-based work, what the rule-based overall ceiling is, ` +
  `which dimensions are ML/Docling-gated, the projected overall /100 after the whole roadmap, and a bottom line.`,
  { label: 'synthesize', phase: 'Synthesize', schema: SYNTH_SCHEMA, effort: 'high' }
)

return { roadmap, plans: plans.map(p => ({
  dimension: p.dimension, root_cause: p.root_cause, needs_ml: p.needs_ml_or_docling,
  expected_after: p.expected_score_after, changes: p.changes,
  challenge_score: p.challenge?.realistic_score_after, cheapest: p.challenge?.cheapest_high_impact_change })) }

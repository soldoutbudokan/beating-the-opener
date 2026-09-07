# Prospective implementation details fixed before activation

This supplements the prospective section of registration
`6b3a072efbefe00b356bc2872b8663bc30163ccb`. No candidate comparison, real
prospective capture or candidate selection has run at this publication.
It does not change the historical comparison or its selection gates.

- Eligible quotes are active main points lines, not marked off, at book 10 or 14.
  Lines must be nonnegative integers or half-integers. The over and under must
  share book, line, player and game; summed implied probability must be in
  [1,1.15]. Both provider side clocks must be within two hours of receipt and
  cannot follow receipt. Provider/receipt clocks and raw bytes are preserved.
- Take first eligible observed receipt per player-game. Equal receipt times
  break by lower book ID, then generation time, then forecast ID. Price advantage
  does not choose among quotes. A failed or late durable write stays in evidence
  but cannot admit a forecast retrospectively.
- Quote receipt follows study start; generation is within five minutes of that
  receipt. Generation is in the final 24 hours and at least 15 minutes before
  scheduled tip. Durable sealing must also finish at least 15 minutes before
  tip. Inputs are available strictly before input cutoff, which cannot follow
  generation. Tip is strictly before the fixed calendar cap.
- The 3,000-forecast endpoint finishes the crossing ET game-tip date. Qualification
  requires at least 2,000 settled played nonpush forecasts across at least 60 ET
  dates with settled nonpush forecasts. Always wait 14 days after endpoint before
  the primary evaluation, including when all outcomes appear settled early.
  More than 1% unresolved admitted forecasts fails settlement quality.
- Select a side at >=5% quoted net expected value before the separate 1% stress,
  including predicted DNP refund probability. If both qualify, select higher EV;
  an exact EV tie selects over. Each chosen nonvoid unit, including a push,
  receives the separate 1% cost/slippage stress. Confirmed DNP has no stake/cost.
  Always-under uses all admitted forecasts as the fixed policy control.
- The primary probability is conditional on playing and on a nonpush result.
  Proportional no-vig prices use the same paired line. Log probabilities are
  clipped at 1e-12. Bootstrap percentile intervals are 95%, with exactly 10,000
  resamples of whole ET game-tip dates, seed 20260908, using ratios of summed
  quantities. Do not refit probabilities or evaluate primary performance early.
- Durable receipts may identify an authorized private Library artifact or Git
  commit. The runtime must verify exact stored bytes and receipt identity;
  caller-supplied timestamps alone do not prove a durable write. Preserve actual
  completion evidence and its clock-assurance limitation. A new capture with no
  events is a valid collection result, but zero forecasts is not candidate
  qualification or proof of a successful forecast-to-settlement cycle.

All other original gates, disclosure restrictions and protected-study boundaries
remain in force. Freeze the concrete candidate and restoreable bundle only after
the bounded comparison and its independent saved-input reproduction.

// Par outstanding reference table for the TRACE volume grid.
// Slow-moving stock data (quarterly at most) — updated manually when SIFMA /
// Treasury publish. Every entry carries its source + as-of so Harry can audit.
// Amounts in $B par. `null` = no clean published aggregate (shown as "—").
// `sharesFloat` marks rows trading against the same underlying float (TBA and
// MBS specified pools both reference agency MBS) so the TOTAL row counts it once.
export const OUTSTANDING = {
  // ---- Treasury ----
  ust:         { amt: 31100, asof: "2Q26", src: "SIFMA Research Quarterly: Fixed Income Outstanding 2Q26 (US Treasury securities)" },
  "ust-bills": { amt: 6700,  asof: "2Q26", src: "SIFMA 2Q26 (bills = 21.5% of UST outstanding)" },
  "ust-coupons": { amt: 21500, asof: "2Q26", src: "SIFMA 2Q26 implied (UST − bills − TIPS − FRNs)" },
  "ust-tips":  { amt: 2200,  asof: "2Q26", src: "Treasury MSPD via SIFMA (approx.)" },
  "ust-frns":  { amt: 700,   asof: "2Q26", src: "Treasury MSPD via SIFMA (approx.)" },
  "ust-onrun": { amt: null,  asof: null,   src: "no published on-the-run stock aggregate" },
  "ust-offrun": { amt: null, asof: null,   src: "no published on-the-run stock aggregate" },
  // ---- TRACE products ----
  tba:  { amt: 9300,  asof: "Dec 2025", src: "Janus Henderson / Bloomberg / BofA (agency MBS)" },
  mbs:  { amt: 9300,  asof: "Dec 2025", src: "same agency-MBS float as TBA (specified pools are the deliverable)", sharesFloat: "tba" },
  corp: { amt: 12100, asof: "2Q26", src: "SIFMA Research Quarterly: Fixed Income Outstanding 2Q26 (corporate bonds)" },
  eln:  { amt: null,  asof: null,   src: "no published equity-linked-note aggregate" },
  conv: { amt: 400,   asof: "mid-2026", src: "ICE BofA All US Convertibles ≈$350B May-25 (Calamos) + record 2025-26 issuance (approx.)" },
  agcy: { amt: 2200,  asof: "2Q26", src: "SIFMA Research Quarterly: Fixed Income Outstanding 2Q26 (federal agency securities)" },
  abs:  { amt: 900,   asof: "Dec 2025", src: "Janus Henderson / Bloomberg (IG ABS; total consumer ABS higher)" },
  absx: { amt: 2700,  asof: "Dec 2025", src: "CLOs $1.2T + non-agency CMBS ~$1.5T (Janus Henderson / Bloomberg / BofA, approx.)" },
  cmo:  { amt: null,  asof: null,   src: "carved from the agency-MBS float; no separate published aggregate" },
  chrc: { amt: null,  asof: null,   src: "no published church-plan bond aggregate" },
};

// Sum unique floats for the synthetic TOTAL row (Treasury + all 10 TRACE
// products), counting shared floats once. Returns { amt, parts }.
export function totalOutstanding(ids) {
  let amt = 0;
  const seen = new Set();
  const parts = [];
  for (const id of ids) {
    const o = OUTSTANDING[id];
    if (!o || o.amt == null) continue;
    const key = o.sharesFloat || id;
    if (seen.has(key)) continue;
    seen.add(key);
    amt += o.amt;
    parts.push(id);
  }
  return { amt, parts };
}

// Short footnote line for the grid footer.
export const OUTSTANDING_NOTE =
  "Outstanding = par outstanding (see column tip per product for source/as-of). " +
  "Turnover = ADV × 252 ÷ outstanding (annualized %). " +
  "TBA and MBS share the agency-MBS float (counted once in TOTAL). " +
  "SIFMA outstanding data is quarterly; table updated manually on release.";

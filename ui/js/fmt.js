export const fmtNum = (x) =>
  x == null ? "—" : x.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

const signed = (x, suffix, digits) => ({
  text: x == null ? "—" : `${x > 0 ? "+" : ""}${x.toFixed(digits)}${suffix}`,
  cls: x == null || x === 0 ? "flat" : x > 0 ? "up" : "down",
});
export const fmtPct = (x) => signed(x, "%", 2);
export const fmtBp = (x) => (x == null ? { text: "—", cls: "flat" } : {
  text: `${x > 0 ? "+" : ""}${x.toFixed(1)}bp`, cls: x === 0 ? "flat" : x > 0 ? "up" : "down",
});

// Ordinal suffix: 1st, 2nd, 3rd, 4th... (11th, 12th, 13th are special cases)
export const ordinal = (n) => {
  if (n == null) return "—";
  const v = Math.round(n);
  const mod100 = v % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${v}th`;
  switch (v % 10) {
    case 1: return `${v}st`;
    case 2: return `${v}nd`;
    case 3: return `${v}rd`;
    default: return `${v}th`;
  }
};

export const fmtClock = (iso) => (iso ? iso.slice(11, 19) : "—");
export const fmtAge = (iso) => {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  return mins < 1 ? "now" : mins < 60 ? `${mins}m ago` : `${Math.round(mins / 60)}h ago`;
};
export const isStale = (iso, maxMinutes) =>
  !iso || (Date.now() - Date.parse(iso)) / 60000 > maxMinutes;

export const fmtUsd = (x) => {
  if (x == null) return "—";
  const abs = Math.abs(x);
  if (abs >= 1e9) return `${(x / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(x / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(x / 1e3).toFixed(1)}K`;
  return x.toFixed(0);
};

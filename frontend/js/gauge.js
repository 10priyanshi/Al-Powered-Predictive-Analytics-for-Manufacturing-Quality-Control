// QualityGuard AI — risk gauge component
// Renders a half-circle gauge with a needle, color-coded to the same
// low/medium/high thresholds used across the rest of the app (teal/amber/rose).

export function gaugeColor(score) {
  if (score > 65) return "#e11d48"; // rose
  if (score > 35) return "#d97706"; // amber
  return "#0f766e"; // teal
}

// Inverse of gaugeColor - for metrics where a HIGH score is GOOD (e.g.
// Batch Quality Score), rather than a high score being bad (risk %).
export function qualityColor(score) {
  if (score >= 66) return "#0f766e"; // teal - good
  if (score >= 33) return "#d97706"; // amber
  return "#e11d48"; // rose - poor
}

export function riskGaugeSVG(score, size = 110) {
  return _gaugeSVG(score, size, gaugeColor(score));
}

// Same half-circle gauge, but colored so a HIGH number reads as GOOD
// (teal), matching the Batch Quality Score's "higher is better" scale -
// the opposite convention from the risk gauge above.
export function qualityGaugeSVG(score, size = 110) {
  return _gaugeSVG(score, size, qualityColor(score));
}

function _gaugeSVG(score, size, color) {
  const clamped = Math.max(0, Math.min(100, score));
  const angle = (clamped / 100) * 180;
  const rad = (Math.PI / 180) * (180 - angle);
  const cx = 60, cy = 58, r = 46;
  const x = cx + r * Math.cos(rad);
  const y = cy - r * Math.sin(rad);
  const arcLength = (clamped / 100) * 144.5;
  const h = Math.round(size * (68 / 120));

  return `
    <svg viewBox="0 0 120 68" width="${size}" height="${h}">
      <path d="M14,58 A46,46 0 0,1 106,58" fill="none" stroke="#e2e8f0" stroke-width="9" stroke-linecap="round" />
      <path d="M14,58 A46,46 0 0,1 106,58" fill="none" stroke="${color}" stroke-width="9"
            stroke-linecap="round" stroke-dasharray="${arcLength} 300" />
      <line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#0f172a" stroke-width="2.5" stroke-linecap="round" />
      <circle cx="${cx}" cy="${cy}" r="4" fill="#0f172a" />
      <text x="60" y="66" text-anchor="middle" font-size="13" font-weight="600" fill="#0f172a">${clamped.toFixed(0)}%</text>
    </svg>
  `;
}

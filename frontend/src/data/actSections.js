// BNS (Bharatiya Nyaya Sanhita 2023) and IT Act 2000 section names — a static
// reference dictionary for the two act codes NOT covered by the backend's
// legal_kb_service (data/legal_kb/ipc_sections.json, served via
// GET /legal/ipc-sections). IPC itself used to have its own table here too —
// replaced by that backend KB so "IPC 302 — Murder" comes from one canonical
// source (also used to answer chat's LEGAL_REFERENCE questions) instead of a
// second, independently-maintained copy — see utils/lookups.js's
// actSectionLabel, which takes the fetched IPC map as a parameter.
//
// BNS section numbers do NOT map 1:1 to IPC numbers (it's a full renumbering,
// not a find-replace) — only the ~30 BNS entries below that are well-documented
// public knowledge are included, rather than guessing at the rest.
const BNS = {
  63: "Rape (definition)",
  64: "Punishment for rape",
  70: "Gang rape",
  74: "Assault or criminal force to a woman with intent to outrage modesty",
  78: "Stalking",
  85: "Cruelty by husband or his relatives",
  101: "Culpable homicide not amounting to murder",
  103: "Punishment for murder",
  105: "Causing death by negligence",
  109: "Attempt to murder",
  111: "Organised crime",
  112: "Petty organised crime",
  115: "Voluntarily causing hurt",
  118: "Voluntarily causing hurt by dangerous weapons",
  137: "Kidnapping",
  140: "Kidnapping for ransom",
  303: "Theft",
  305: "Theft in a dwelling house",
  308: "Extortion",
  309: "Robbery",
  310: "Dacoity",
  316: "Criminal breach of trust",
  317: "Dishonestly receiving stolen property",
  318: "Cheating",
  324: "Mischief",
  329: "Criminal trespass / house-trespass",
  336: "Forgery",
  338: "Forgery for the purpose of cheating",
  340: "Using as genuine a forged document",
  351: "Criminal intimidation",
  356: "Defamation",
};

const IT_ACT = {
  43: "Penalty for damage to computer / computer system",
  65: "Tampering with computer source documents",
  66: "Computer-related offences",
  "66B": "Receiving stolen computer resource or communication device",
  "66C": "Identity theft",
  "66D": "Cheating by personation using a computer resource",
  "66E": "Violation of privacy",
  "66F": "Cyber terrorism",
  67: "Publishing obscene material in electronic form",
  "67A": "Publishing sexually explicit material in electronic form",
  72: "Breach of confidentiality and privacy",
};

// ActCode is stored as "IT" in the live data (ActSectionAssociation.ActCode) —
// keyed to match exactly. No "IPC" entry here on purpose — see module comment.
const ACT_TABLES = { BNS, IT: IT_ACT };

export function lookupActSection(actCode, sectionCode) {
  const table = ACT_TABLES[String(actCode || "").trim().toUpperCase()];
  if (!table) return null;
  const key = String(sectionCode || "").trim().toUpperCase();
  return table[key] || null;
}

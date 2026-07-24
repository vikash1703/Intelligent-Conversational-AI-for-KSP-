"""One-time/re-runnable generator: builds the QuickML Knowledge Base upload
document (rag_documents/02_ipc_bns_common_sections_reference.txt) FROM the
canonical JSON KB (data/legal_kb/*.json) — one source of truth, not a second
manually-maintained copy of the same legal content.

There is no Catalyst API to upload a document to the QuickML Knowledge Base
(confirmed against docs.catalyst.zoho.com — upload is Desktop/WorkDrive/Zoho
Learn only, console-side). This script produces the file; a human still has
to upload it via the Catalyst console (Knowledge Base > Upload), then copy
its new Document ID into .env's ZOHO_DOCUMENT_IDS.

Re-run this after any edit to data/legal_kb/*.json, then re-upload.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "data" / "legal_kb"
OUT_PATH = ROOT / "rag_documents" / "02_ipc_bns_common_sections_reference.txt"

with open(KB_DIR / "ipc_sections.json", encoding="utf-8") as f:
    sections = json.load(f)

with open(KB_DIR / "procedures.json", encoding="utf-8") as f:
    procedures = json.load(f)

lines = []
lines.append("IPC / BNS LEGAL REFERENCE — SECTIONS AND PROCEDURES")
lines.append(
    "(General legal reference for chatbot Q&A. This is a summary aid only — for "
    "a specific case, the authoritative Act/Section values always come from the "
    "live database's Act and Section tables, not this document. For operational "
    "reference; verify against the official text.)"
)
lines.append("")
lines.append(
    "IMPORTANT NOTE ON IPC vs BNS: India's Indian Penal Code, 1860 (IPC) was "
    "replaced by the Bharatiya Nyaya Sanhita, 2023 (BNS) with effect from 1 July "
    "2024. Cases registered before that date are recorded under IPC section "
    "numbers; cases registered on/after that date use BNS section numbers. When "
    "a user asks 'what is Section X' with no other qualifier, treat it as an IPC "
    "section number unless they say BNS or the case context indicates otherwise."
)
lines.append("")
lines.append("=" * 78)
lines.append("PART 1: IPC SECTIONS")
lines.append("=" * 78)
lines.append("")

for s in sections:
    lines.append(f"--- IPC SECTION {s['section_no']} — {s['title']} ---")
    lines.append(f"Plain-language meaning: {s['plain_language_summary']}")
    lines.append(f"Punishment: {s['punishment']}")
    lines.append(f"Cognizable: {s['cognizable']}")
    lines.append(f"Bailable: {s['bailable']}")
    lines.append(f"BNS equivalent: {s['bns_equivalent']}")
    lines.append("")

lines.append("=" * 78)
lines.append("PART 2: PROCEDURAL CONCEPTS")
lines.append("=" * 78)
lines.append("")

for p in procedures:
    lines.append(f"--- CONCEPT: {p['concept']} ---")
    lines.append(p["plain_language_explanation"])
    lines.append("")

OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {OUT_PATH} ({len(sections)} sections, {len(procedures)} procedures)")

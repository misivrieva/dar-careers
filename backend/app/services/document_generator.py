"""ATS-friendly DOCX rendering and safe application folder layout.

applications/<user_id>/<employer>/<job_title>_<job_id>/{CV.docx, Cover_Letter.docx, Job_Description.txt}
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from docx import Document as Docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from ..config import get_settings


def safe_component(name: str | None, max_len: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9 _-]+", "_", name or "")
    s = re.sub(r"[\s_]+", "_", s).strip("._- ")
    return (s[:max_len].rstrip("._-") or "unnamed")


def application_folder(user_id: int, employer_name: str, job_title: str, job_id: int) -> Path:
    base = (get_settings().storage_path / "applications").resolve()
    folder = (base / str(int(user_id)) / safe_component(employer_name) / f"{safe_component(job_title)}_{int(job_id)}").resolve()
    if base not in folder.parents:
        raise ValueError("unsafe application path")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def ensure_within(path: Path, base: Path) -> Path:
    p, b = Path(path).resolve(), Path(base).resolve()
    if b != p and b not in p.parents:
        raise PermissionError("path outside storage")
    return p


def _base_doc() -> Docx:
    d = Docx()
    st = d.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    for s in d.sections:
        s.left_margin = s.right_margin = Pt(54)
        s.top_margin = s.bottom_margin = Pt(48)
    return d


def render_cv(path: Path, cv: dict) -> None:
    d = _base_doc()
    h = d.add_paragraph()
    r = h.add_run(cv.get("name") or "Candidate")
    r.bold = True
    r.font.size = Pt(18)
    contact = " | ".join(x for x in cv.get("contact", []) if x)
    if contact:
        d.add_paragraph(contact)
    d.add_heading("Professional Summary", level=2)
    d.add_paragraph(cv["professional_summary"])
    if cv.get("key_skills"):
        d.add_heading("Key Skills", level=2)
        d.add_paragraph(", ".join(cv["key_skills"]))
    d.add_heading("Experience", level=2)
    for role in cv["roles"]:
        p = d.add_paragraph()
        p.add_run(f"{role['title']}").bold = True
        p.add_run(f" — {role['employer']}")
        dates = f"{role.get('start') or ''} – {role.get('end') or ''}".strip(" –")
        if dates or role.get("location"):
            meta = d.add_paragraph(" | ".join(x for x in [dates, role.get("location")] if x))
            meta.runs[0].italic = True
        for b in role["bullets"]:
            d.add_paragraph(b["text"], style="List Bullet")
    if cv.get("education"):
        d.add_heading("Education", level=2)
        for e in cv["education"]:
            d.add_paragraph(" ".join(x for x in [e.get("qualification"), f"in {e['field']}" if e.get("field") else None,
                                                 "—", e.get("institution"), e.get("end")] if x))
    if cv.get("certifications"):
        d.add_heading("Certifications", level=2)
        for c in cv["certifications"]:
            d.add_paragraph(" ".join(x for x in [c.get("name"), f"({c['date']})" if c.get("date") else None] if x), style="List Bullet")
    d.save(str(path))


def render_letter(path: Path, letter: dict, name: str | None) -> None:
    d = _base_doc()
    d.add_paragraph(letter["greeting"])
    for p in letter["paragraphs"]:
        para = d.add_paragraph(p["text"])
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    d.add_paragraph(letter["closing"])
    if name:
        d.add_paragraph(name)
    d.save(str(path))


def cv_plain_text(cv: dict) -> str:
    lines = [cv.get("professional_summary", ""), "Skills: " + ", ".join(cv.get("key_skills", []))]
    for r in cv["roles"]:
        lines.append(f"{r['title']} — {r['employer']} ({r.get('start')} – {r.get('end')})")
        lines += [f"- {b['text']}" for b in r["bullets"]]
    return "\n".join(lines)


def letter_plain_text(letter: dict) -> str:
    return "\n\n".join([letter["greeting"]] + [p["text"] for p in letter["paragraphs"]] + [letter["closing"]])


def zip_folder(files: list[Path], arc_root: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f"{safe_component(arc_root)}/{f.name}")
    return buf.getvalue()

import re
from pathlib import Path
from zipfile import BadZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
import fitz


EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
SKILL_TERMS = {
    "python", "java", "javascript", "typescript", "react", "fastapi", "sql", "postgresql",
    "aws", "azure", "docker", "kubernetes", "excel", "project management", "data analysis",
    "communication", "leadership", "machine learning", "c++", "go", "node.js",
}


def _docx_text(document) -> str:
    """Paragraphs, tables (many resume templates are tables) and headers, in document order."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    lines: list[str] = []

    def table_lines(table: Table) -> None:
        for row in table.rows:
            seen = []
            for cell in row.cells:
                value = " ".join(part.text.strip() for part in cell.paragraphs if part.text.strip())
                if value and value not in seen:
                    seen.append(value)
            if seen:
                lines.append("  ".join(seen))

    for section in document.sections:
        lines.extend(paragraph.text for paragraph in section.header.paragraphs if paragraph.text.strip())
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, document).text)
        elif tag == "tbl":
            table_lines(Table(child, document))
    return "\n".join(lines)


def extract_text(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".txt":
            text = content.decode("utf-8-sig", errors="strict")
        elif extension == ".pdf":
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("The PDF is password protected.")
                text = "\n".join(page.get_text("text", sort=True) for page in document)
                if not text.strip() and any(page.get_images() for page in document):
                    raise ValueError("The PDF appears to be a scan or image-only file; text could not be extracted (OCR is not enabled).")
        elif extension == ".docx":
            from io import BytesIO

            text = _docx_text(Document(BytesIO(content)))
        else:
            raise ValueError("Unsupported resume format. Upload a PDF, DOCX, or TXT file.")
    except (fitz.FileDataError, RuntimeError, BadZipFile, PackageNotFoundError, UnicodeDecodeError, KeyError):
        raise ValueError("The resume is unreadable or damaged.")

    if not text.strip():
        raise ValueError("The resume does not contain readable text.")
    return text[:100_000]


def parse_resume(text: str) -> dict:
    lowered = text.lower()
    years = re.search(r"(?<!\d)(\d{1,2})\+?\s+years?(?:\s+of)?\s+(?:experience| exp)", lowered)
    sections = split_resume_sections(text)
    education_section = sections.get("education", "")
    education_lines = [
        line.strip()[:500]
        for line in text.splitlines()
        if any(term in line.lower() for term in ("bachelor", "master", "ph.d", "b.sc", "m.sc", "degree", "university", "college"))
    ]
    return {
        "email": (EMAIL_RE.search(text).group(0) if EMAIL_RE.search(text) else None),
        "phone": (PHONE_RE.search(text).group(0).strip() if PHONE_RE.search(text) else None),
        "skills": extract_skills(text, sections),
        "experience_years": float(years.group(1)) if years else None,
        "education": education_section[:1_500] or "\n".join(education_lines[:10]) or None,
        "experience": sections.get("experience", "")[:12_000],
        "projects": sections.get("projects", "")[:12_000],
        "internships": sections.get("internships", "")[:12_000],
        "certifications": sections.get("certifications", "")[:6_000],
    }


SECTION_NAMES = {
    "internships": ("internship", "internships", "industrial training", "industrial experience", "trainee", "apprenticeship"),
    "experience": ("experience", "employment", "work history", "professional experience", "work experience",
                   "employment history", "career history", "professional background", "work"),
    "projects": ("projects", "project", "selected work", "portfolio", "academic projects", "personal projects",
                 "key projects", "technical projects", "project experience"),
    "education": ("education", "academic", "academics", "academic background", "qualifications",
                  "educational qualifications", "academic qualifications"),
    "certifications": ("certifications", "certificates", "licenses", "courses", "training and certifications"),
    "skills": ("skills", "technologies", "technical stack", "technical skills", "core competencies", "tech stack",
               "tools", "tools and technologies", "key skills", "skills summary", "areas of expertise"),
}
HEADING_LAST_WORDS = {
    "skills": "skills", "skill": "skills", "technologies": "skills", "competencies": "skills",
    "experience": "experience", "projects": "projects", "internships": "internships", "internship": "internships",
    "education": "education", "certifications": "certifications", "certificates": "certifications",
}


def _heading_section(line: str) -> str | None:
    """Map a heading line such as 'TECHNICAL SKILLS:' or 'Academic Projects' to a section name."""
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return None
    heading = re.sub(r"[^a-z& ]+", " ", stripped.lower()).replace("&", " and ")
    heading = re.sub(r"\s+", " ", heading).strip()
    words = heading.split()
    if not words or len(words) > 5:
        return None
    for name, aliases in SECTION_NAMES.items():
        if heading in aliases:
            return name
    if len(words) <= 4 and words[-1] in HEADING_LAST_WORDS and not stripped.endswith("."):
        # e.g. "Work Experience in Retail", "Key Technical Skills" - short title-like lines only.
        return HEADING_LAST_WORDS[words[-1]]
    return None


def split_resume_sections(text: str) -> dict[str, str]:
    current = "general"
    sections: dict[str, list[str]] = {current: []}
    for line in text.splitlines():
        matched = _heading_section(line)
        if matched:
            current = matched
            sections.setdefault(current, [])
        elif line.strip():
            sections.setdefault(current, []).append(line.strip())

    return {
        name: "\n".join(dict.fromkeys(lines))
        for name, lines in sections.items()
        if lines
    }


def extract_skills(text: str, sections: dict[str, str]) -> list[str]:
    """Skills from the skills section (any skill, not a fixed list) plus well-known terms found anywhere."""
    from app.services.skills import canonical, find_skill, split_skill_list

    found = split_skill_list(sections.get("skills", ""))
    found += [canonical(term) for term in SKILL_TERMS if find_skill(text, term) and find_skill(text, term)[0] != "fuzzy"]
    return sorted(dict.fromkeys(found))[:80]


def resume_chunks(text: str, max_characters: int = 1_200) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    for section, content in split_resume_sections(text).items():
        paragraphs = [part.strip() for part in re.split(r"\n{2,}", content) if part.strip()]
        buffer = ""
        index = 0
        for paragraph in paragraphs:
            if len(paragraph) > max_characters:
                if buffer:
                    chunks.append({"section": section, "chunk_id": str(index), "text": buffer})
                    index += 1
                    buffer = ""
                for start in range(0, len(paragraph), max_characters):
                    chunk = paragraph[start:start + max_characters].strip()
                    if chunk:
                        chunks.append({"section": section, "chunk_id": str(index), "text": chunk})
                        index += 1
            elif len(buffer) + len(paragraph) + 2 > max_characters:
                chunks.append({"section": section, "chunk_id": str(index), "text": buffer})
                index += 1
                buffer = paragraph
            else:
                buffer = f"{buffer}\n\n{paragraph}".strip()
        if buffer:
            chunks.append({"section": section, "chunk_id": str(index), "text": buffer})
    return chunks

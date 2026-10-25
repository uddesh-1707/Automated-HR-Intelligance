"""Skill normalisation: abbreviations, synonyms and spelling variants, shared by parsing and matching.

Synonyms are data, not logic: extend them without code changes through ``SKILL_SYNONYMS`` in ``.env``
(JSON object of ``{"canonical skill": ["alias", ...]}``).
"""

import re
from difflib import SequenceMatcher

from app.core.config import settings

BUILTIN_SYNONYMS: dict[str, list[str]] = {
    "javascript": ["js", "java script", "ecmascript", "es6"],
    "typescript": ["ts"],
    "python": ["py", "python3"],
    "machine learning": ["ml"],
    "deep learning": ["dl"],
    "natural language processing": ["nlp"],
    "large language models": ["llm", "llms", "large language model"],
    "generative ai": ["genai", "gen ai", "generative artificial intelligence"],
    "artificial intelligence": ["ai"],
    "node.js": ["node", "nodejs", "node js"],
    "react": ["reactjs", "react.js", "react js"],
    "next.js": ["nextjs", "next js"],
    "vue": ["vuejs", "vue.js"],
    "postgresql": ["postgres", "psql"],
    "mongodb": ["mongo", "mongo db"],
    "kubernetes": ["k8s"],
    "aws": ["amazon web services"],
    "gcp": ["google cloud platform", "google cloud"],
    "azure": ["microsoft azure"],
    "ci/cd": ["cicd", "ci cd", "continuous integration", "continuous delivery"],
    "data structures and algorithms": ["dsa", "data structures", "data structure", "algorithms"],
    "rest api": ["restful api", "rest apis", "restful apis", "restful services"],
    "automation": ["workflow automation", "process automation", "rpa", "automated workflows"],
    "langchain": ["lang chain"],
    "prompt engineering": ["prompting", "prompt design"],
    "c++": ["cpp"],
    "c#": ["csharp", "c sharp"],
    "power bi": ["powerbi"],
    "excel": ["ms excel", "microsoft excel"],
    "data analysis": ["data analytics", "analyzing data", "analysing data"],
    "project management": ["project manager", "managed projects"],
    "communication": ["communication skills", "written communication", "verbal communication"],
    "docker": ["containerization", "containerisation", "containers"],
    "fastapi": ["fast api"],
    "sql": ["structured query language"],
    "n8n": ["n8n.io"],
}


def _clean(term: str) -> str:
    term = re.sub(r"\s+", " ", term.strip().casefold())
    return term.strip(" .,;:()[]{}\"'") if term not in {"c++", "c#"} else term


def _alias_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    merged = {**BUILTIN_SYNONYMS}
    for canonical, aliases in settings.skill_synonyms.items():
        merged[canonical] = [*merged.get(canonical, []), *aliases]
    for canonical, aliases in merged.items():
        mapping[_clean(canonical)] = _clean(canonical)
        for alias in aliases:
            mapping[_clean(alias)] = _clean(canonical)
    return mapping


def canonical(term: str) -> str:
    cleaned = _clean(term)
    return _alias_map().get(cleaned, cleaned)


def variants(term: str) -> set[str]:
    """Every surface form that should count as this skill."""
    target = canonical(term)
    forms = {target, _clean(term)}
    forms.update(alias for alias, canon in _alias_map().items() if canon == target)
    return {form for form in forms if form}


def _boundary(form: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(form).replace(r'\ ', r'[\s\-]+')}(?![A-Za-z0-9+#])", re.IGNORECASE)


def excerpt_around(text: str, start: int, end: int, radius: int = 110) -> str:
    left = max(0, text.rfind("\n", 0, start), start - radius)
    right_newline = text.find("\n", end)
    right = min(len(text), end + radius if right_newline == -1 else min(right_newline, end + radius))
    return re.sub(r"\s+", " ", text[left:right]).strip()


def find_skill(text: str, term: str) -> tuple[str, str] | None:
    """Locate a skill in text. Returns (method, excerpt) with method exact | synonym | fuzzy, or None."""
    wanted = _clean(term)
    for form in sorted(variants(term), key=len, reverse=True):
        match = _boundary(form).search(text)
        if match:
            return ("exact" if form == wanted else "synonym"), excerpt_around(text, match.start(), match.end())
    if " " not in wanted and len(wanted) >= 5:
        for match in re.finditer(r"[A-Za-z][A-Za-z0-9.+#]{4,}", text):
            if abs(len(match.group(0)) - len(wanted)) <= 2 and SequenceMatcher(None, wanted, match.group(0).casefold()).ratio() >= 0.88:
                return "fuzzy", excerpt_around(text, match.start(), match.end())
    return None


def split_skill_list(section: str) -> list[str]:
    """Tokens from a 'Skills' section: 'Python, SQL | React' -> ['python', 'sql', 'react']."""
    found: list[str] = []
    for line in section.splitlines():
        line = re.sub(r"^[\s\u2022\u25cf\u25aa\-*]+", "", line)
        if ":" in line and len(line.split(":", 1)[0]) <= 40:
            line = line.split(":", 1)[1]
        for part in re.split(r"[,;|\u2022/]+|\s{2,}", line):
            token = _clean(part)
            if 1 < len(token) <= 40 and len(token.split()) <= 4:
                found.append(canonical(token))
    return list(dict.fromkeys(found))

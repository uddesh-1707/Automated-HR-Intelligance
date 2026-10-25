import logging
import re
import time
from typing import Protocol

from app.core.config import settings
from app.services.skills import canonical, find_skill

logger = logging.getLogger(__name__)
SCORING_VERSION = "hybrid-v1"
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")


class MatchingNotConfigured(RuntimeError):
    pass


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorIndex(Protocol):
    def upsert(self, vectors: list[dict]) -> None: ...
    def query(
        self,
        vector: list[float],
        *,
        application_id: str | None = None,
        job_id: str | None = None,
        top_k: int,
    ) -> list[dict]: ...


class OpenAIEmbeddingClient:
    def __init__(self) -> None:
        if not settings.embedding_api_key:
            raise MatchingNotConfigured("Set EMBEDDING_API_KEY to enable semantic matching.")
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.embedding_api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
            dimensions=settings.embedding_dimensions,
        )
        return [item.embedding for item in response.data]


class PineconeVectorIndex:
    def __init__(self) -> None:
        if not settings.pinecone_api_key:
            raise MatchingNotConfigured("Set PINECONE_API_KEY to enable semantic matching.")
        from pinecone import Pinecone, ServerlessSpec

        self.client = Pinecone(api_key=settings.pinecone_api_key)
        self.name = settings.pinecone_index_name
        if not self.client.has_index(self.name):
            self.client.create_index(
                name=self.name,
                dimension=settings.embedding_dimensions,
                metric="cosine",
                spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region),
            )
        self.index = self.client.Index(self.name)

    def upsert(self, vectors: list[dict]) -> None:
        self.index.upsert(vectors=vectors)

    def query(
        self,
        vector: list[float],
        *,
        application_id: str | None = None,
        job_id: str | None = None,
        top_k: int,
    ) -> list[dict]:
        metadata_filter = {"embedding_model_version": {"$eq": settings.embedding_model_version}}
        if application_id:
            metadata_filter["application_id"] = {"$eq": application_id}
        if job_id:
            metadata_filter["job_id"] = {"$eq": job_id}
        response = self.index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter,
        )
        return [
            {
                "score": float(match.score or 0),
                "metadata": dict(match.metadata or {}),
            }
            for match in response.matches
        ]

    def delete_application_vectors(self, application_id: str) -> None:
        self.index.delete(
            filter={
                "application_id": {"$eq": application_id},
                "embedding_model_version": {"$eq": settings.embedding_model_version},
            }
        )


def _text_list(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value and value.strip()]


def _criterion_queries(job: dict, parsed: dict) -> dict[str, str]:
    required_skills = _text_list(job.get("required_skills", []))
    responsibilities = _text_list(job.get("responsibilities", []))
    preferred = _text_list(job.get("preferred_skills", [])) + _text_list(job.get("preferred_qualifications", []))
    education = _text_list(job.get("education_requirements", []))
    experience = []
    if job.get("minimum_experience") is not None:
        experience.append(f"At least {job['minimum_experience']} years of relevant work experience")
    if job.get("description"):
        responsibilities.append(job["description"].strip())

    queries = {}
    if required_skills:
        queries["skills"] = "Required skills and professional capability: " + ", ".join(required_skills)
    if responsibilities:
        queries["responsibilities"] = "Relevant responsibilities and work: " + "\n".join(responsibilities[:15])
    if experience or parsed.get("projects") or parsed.get("experience"):
        queries["experience_projects"] = "\n".join(
            experience + ["Relevant projects and work experience for " + (job.get("title") or "this role")]
        )
    if education:
        queries["education"] = "Required education and qualifications: " + ", ".join(education)
    if preferred:
        queries["preferred"] = "Preferred skills and qualifications: " + ", ".join(preferred)
    return queries


def _clamp_percent(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _match_score(criterion: str, job: dict, parsed: dict, evidence: list[dict]) -> float:
    from app.services.skills import canonical

    candidate_skills = {canonical(skill) for skill in parsed.get("skills", [])}
    exact_required = {canonical(skill) for skill in job.get("required_skills", []) if skill.strip()}
    exact_preferred = {
        canonical(skill)
        for skill in (job.get("preferred_skills", []) + job.get("preferred_qualifications", []))
        if skill.strip()
    }

    if criterion == "skills":
        if not exact_required:
            return max((item["semantic_similarity"] for item in evidence), default=0) * 100
        explicit = len(candidate_skills & exact_required) / len(exact_required)
        semantic = max((item["semantic_similarity"] for item in evidence), default=0)
        return _clamp_percent(explicit * 80 + semantic * 20)
    if criterion == "preferred":
        if not exact_preferred:
            return max((item["semantic_similarity"] for item in evidence), default=0) * 100
        exact = len(candidate_skills & exact_preferred) / len(exact_preferred)
        semantic = max((item["semantic_similarity"] for item in evidence), default=0)
        return _clamp_percent(exact * 70 + semantic * 30)
    if criterion == "education":
        requirements = [value.casefold() for value in job.get("education_requirements", [])]
        education = (parsed.get("education") or "").casefold()
        if requirements and education:
            explicit = any(value in education for value in requirements)
            semantic = max((item["semantic_similarity"] for item in evidence), default=0)
            return _clamp_percent((80 if explicit else 0) + semantic * 20)
    if criterion == "experience_projects":
        semantic = max((item["semantic_similarity"] for item in evidence), default=0)
        minimum = job.get("minimum_experience")
        years = parsed.get("experience_years")
        if minimum is not None and years is not None:
            explicit = 100.0 if years >= minimum else (years / minimum * 100 if minimum else 100.0)
            return _clamp_percent(explicit * 0.5 + semantic * 100 * 0.5)
    return _clamp_percent(max((item["semantic_similarity"] for item in evidence), default=0) * 100)


def calculate_ats_result(
    *,
    job: dict,
    parsed: dict,
    resume_text: str,
    embeddings: EmbeddingClient,
    vectors: VectorIndex,
    application_id: str,
) -> dict:
    weights = job.get("scoring_weights") or {
        "skills": 30.0,
        "experience_projects": 25.0,
        "responsibilities": 25.0,
        "education": 10.0,
        "preferred": 10.0,
    }
    if round(sum(weights.values()), 4) != 100:
        raise ValueError("Job scoring weights must total 100 before matching can run.")

    from app.services.resume_parser import resume_chunks

    safe_text = EMAIL_RE.sub("[email removed]", resume_text)
    safe_text = PHONE_RE.sub("[phone removed]", safe_text)
    candidate_name = str(parsed.get("candidate_name", "")).strip()
    if candidate_name:
        safe_text = re.sub(re.escape(candidate_name), "[candidate]", safe_text, flags=re.IGNORECASE)
    chunks = resume_chunks(safe_text)
    if not chunks:
        raise ValueError("No readable resume sections were found for semantic matching.")
    chunk_vectors = embeddings.embed([chunk["text"] for chunk in chunks])
    if len(chunk_vectors) != len(chunks):
        raise RuntimeError("Embedding service returned an unexpected number of resume vectors.")
    vectors.upsert([
        {
            "id": f"{application_id}:{settings.embedding_model_version}:{chunk['section']}:{chunk['chunk_id']}",
            "values": vector,
            "metadata": {
                "application_id": application_id,
                "candidate_id": parsed["candidate_id"],
                "job_id": job["_id"],
                "section": chunk["section"],
                "chunk_id": chunk["chunk_id"],
                "embedding_model_version": settings.embedding_model_version,
                "text": chunk["text"],
            },
        }
        for chunk, vector in zip(chunks, chunk_vectors, strict=True)
    ])

    criterion_queries = _criterion_queries(job, parsed)
    query_names = list(criterion_queries)
    query_vectors = embeddings.embed([criterion_queries[name] for name in query_names]) if query_names else []
    evidence_by_criterion: dict[str, list[dict]] = {}
    for name, vector in zip(query_names, query_vectors, strict=True):
        matches = vectors.query(vector, application_id=application_id, top_k=3)
        evidence_by_criterion[name] = [
            {
                "criterion": name,
                "section": item["metadata"].get("section", "resume"),
                "excerpt": str(item["metadata"].get("text", ""))[:700],
                "semantic_similarity": round(item["score"], 4),
                "explanation": f"Retrieved as relevant evidence for {name.replace('_', ' ')}; HR should verify the excerpt.",
            }
            for item in matches
            if item["metadata"].get("text")
        ]

    criterion_scores = {}
    for criterion, weight in weights.items():
        evidence = evidence_by_criterion.get(criterion, [])
        score = _match_score(criterion, job, parsed, evidence)
        criterion_scores[criterion] = {
            "score": score,
            "weight": weight,
            "weighted_score": round(score * weight / 100, 2),
            "basis": "hybrid structured and semantic evidence" if evidence else "structured resume fields",
        }
    overall = round(sum(item["weighted_score"] for item in criterion_scores.values()), 1)

    resume_lower = resume_text.casefold()
    from app.services.skills import find_skill

    mandatory = [
        {
            "criterion": criterion,
            "status": "verified" if (criterion.casefold() in resume_lower or find_skill(resume_text, criterion)) else "not_verified",
            "note": (
                "Supporting text (including accepted synonyms or abbreviations) was found in the resume."
                if (criterion.casefold() in resume_lower or find_skill(resume_text, criterion))
                else "No supporting text was found; this is missing evidence, not proof the requirement is unmet. HR review is required."
            ),
        }
        for criterion in _text_list(job.get("mandatory_criteria", []))
    ]
    missing = [
        f"Required skill not explicitly listed: {skill}"
        for skill in _text_list(job.get("required_skills", []))
        if canonical(skill) not in {canonical(item) for item in parsed.get("skills", [])} and not find_skill(resume_text, skill)
    ]
    years = parsed.get("experience_years")
    if job.get("minimum_experience") is not None and years is None:
        missing.append(f"Experience requirement not verified: {job['minimum_experience']} years minimum")
    unverified_mandatory = any(item["status"] != "verified" for item in mandatory)
    confidence_evidence = [
        item for values in evidence_by_criterion.values() for item in values
        if item["semantic_similarity"] >= 0.55
    ]
    confidence = "high" if len(confidence_evidence) >= 5 else "medium" if len(confidence_evidence) >= 2 else "low"
    return {
        "ats_score": overall,
        "criterion_scores": criterion_scores,
        "matching_evidence": [
            item for values in evidence_by_criterion.values() for item in values
        ],
        "missing_criteria": missing,
        "mandatory_criteria_results": mandatory,
        "recommendation": "review_mandatory_criteria" if unverified_mandatory else "recommended_for_hr_review",
        "confidence": confidence,
        "scoring_version": settings.matching_scoring_version,
        "embedding_model": settings.embedding_model,
        "embedding_model_version": settings.embedding_model_version,
        "matching_status": "completed",
    }

from app.services.semantic_matching import calculate_ats_result


class FakeEmbeddings:
    def embed(self, texts):
        return [[0.2, 0.4, 0.6] for _ in texts]


class FakeVectorIndex:
    def __init__(self):
        self.upserted = []

    def upsert(self, vectors):
        self.upserted.extend(vectors)

    def query(self, vector, *, application_id=None, job_id=None, top_k):
        assert application_id == "application-1"
        return [{
            "score": 0.82,
            "metadata": {
                "section": "projects",
                "text": "Built a coordinator-worker scheduler using gRPC and PostgreSQL.",
            },
        }]


def test_semantic_matching_uses_evidence_and_emits_explainable_weighted_score():
    index = FakeVectorIndex()
    result = calculate_ats_result(
        job={
            "_id": "job-1",
            "title": "Platform Engineer",
            "description": "Develop reliable distributed systems.",
            "required_skills": ["Python", "Kubernetes"],
            "preferred_skills": ["AWS"],
            "responsibilities": ["Design reliable services"],
            "minimum_experience": 4,
            "education_requirements": ["Bachelor"],
            "mandatory_criteria": ["security clearance"],
            "scoring_weights": {
                "skills": 30,
                "experience_projects": 25,
                "responsibilities": 25,
                "education": 10,
                "preferred": 10,
            },
        },
        parsed={
            "candidate_id": "candidate-1",
            "candidate_name": "Taylor Candidate",
            "skills": ["python"],
            "experience_years": 5,
            "education": "Bachelor degree",
            "projects": "Created scheduling platform",
        },
        resume_text=(
            "Taylor Candidate\n"
            "taylor@example.com\n"
            "+1 555-222-1111\n"
            "Projects\n"
            "Built a coordinator-worker scheduler using gRPC and PostgreSQL."
        ),
        embeddings=FakeEmbeddings(),
        vectors=index,
        application_id="application-1",
    )
    assert 0 <= result["ats_score"] <= 100
    assert sum(item["weight"] for item in result["criterion_scores"].values()) == 100
    assert result["matching_status"] == "completed"
    assert result["mandatory_criteria_results"][0]["status"] == "not_verified"
    assert result["recommendation"] == "review_mandatory_criteria"
    assert "scheduler" in result["matching_evidence"][0]["excerpt"]
    assert len(index.upserted) >= 1
    for vector in index.upserted:
        vector_metadata = vector["metadata"]
        assert "taylor@example.com" not in vector_metadata["text"]
        assert "555-222-1111" not in vector_metadata["text"]
        assert "Taylor Candidate" not in vector_metadata["text"]

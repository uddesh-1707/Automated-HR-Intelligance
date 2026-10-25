from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.evaluations import router as evaluations_router
from app.api.v1.interviews import router as interviews_router
from app.api.v1.interview_workflow import router as interview_workflow_router
from app.api.v1.recruitment import router as recruitment_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(recruitment_router, prefix="/recruitment", tags=["recruitment"])
api_router.include_router(evaluations_router, prefix="/evaluations", tags=["interviewer evaluation (token access)"])
api_router.include_router(interviews_router, prefix="/interviews", tags=["interviews"])
api_router.include_router(interview_workflow_router, prefix="/interviews", tags=["interview workflow"])

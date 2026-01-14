"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", summary="Health check")
async def health_check():
    """
    Health check endpoint.
    
    Returns the health status of the API service.
    """
    return {"status": "healthy"}

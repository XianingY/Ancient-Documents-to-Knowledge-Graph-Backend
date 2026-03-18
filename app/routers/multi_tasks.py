
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db, User
from app.core.deps import get_current_user_id, get_current_user
from app.services.multi_task_service import (
    create_multi_task, 
    delete_multi_task, 
    get_multi_task, 
    find_latest_structured_results_for_images,
    get_multi_task_relation_ids,
    get_multi_task_structured_result_ids,
    MultiTaskServiceError,
    ResourceNotFoundError,
    PermissionDeniedError
)
from app.schemas.multi_tasks import CreateMultiTaskRequest, CreateMultiTaskByImagesRequest

router = APIRouter(prefix="/api/v1/multi-tasks", tags=["Multi Tasks"])

@router.post("")
def create_multi_task_endpoint(
    request: CreateMultiTaskRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id)
):
    try:
        task = create_multi_task(db, user_id, request.structured_result_ids)
        return {
            "success": True,
            "message": "Multi task created successfully",
            "multi_task_id": task.id,
            "structured_result_ids": request.structured_result_ids,
            "created_at": task.created_at.isoformat()
        }
    except MultiTaskServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/from-images")
def create_multi_task_from_images_endpoint(
    request: CreateMultiTaskByImagesRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id)
):
    try:
        structured_result_ids = find_latest_structured_results_for_images(db, request.image_ids, user_id=user_id)
        task = create_multi_task(db, user_id, structured_result_ids)
        return {
            "success": True,
            "message": "Multi task created from images successfully",
            "multi_task_id": task.id,
            "image_ids": request.image_ids,
            "structured_result_ids": structured_result_ids,
            "created_at": task.created_at.isoformat()
        }
    except MultiTaskServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{multi_task_id}")
def get_multi_task_endpoint(
    multi_task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = get_multi_task(db, multi_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="MultiTask not found")
    
    if task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")
    
    sr_ids = get_multi_task_structured_result_ids(db, multi_task_id)
    
    return {
        "success": True,
        "data": {
            "id": task.id,
            "user_id": task.user_id,
            "status": task.status.value,
            "structured_result_ids": sr_ids,
            "created_at": task.created_at.isoformat(),
        }
    }

@router.delete("/{multi_task_id}")
def delete_multi_task_endpoint(
    multi_task_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id)
):
    try:
        result = delete_multi_task(db, multi_task_id, user_id)
        return {
            "success": True,
            "message": "Multi task deleted",
            "deleted": result
        }
    except ResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except MultiTaskServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{multi_task_id}/multi-relation-graphs")
def get_multi_task_relation_graphs_endpoint(
    multi_task_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = get_multi_task(db, multi_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="MultiTask not found")
    
    if task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")

    ids, total = get_multi_task_relation_ids(db, multi_task_id, skip, limit)
    
    return {
        "success": True,
        "data": {"total": total, "skip": skip, "limit": limit, "ids": ids},
    }

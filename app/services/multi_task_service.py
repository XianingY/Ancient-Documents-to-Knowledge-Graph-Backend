
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from database import MultiTask, MultiTaskStructuredResult, StructuredResult, MultiRelationGraph, Image, OcrResult, get_beijing_time

class MultiTaskServiceError(Exception):
    """Base exception for multi task service"""
    pass

class ResourceNotFoundError(MultiTaskServiceError):
    pass

class PermissionDeniedError(MultiTaskServiceError):
    pass

def get_multi_task(db: Session, multi_task_id: int) -> Optional[MultiTask]:
    return db.query(MultiTask).filter(MultiTask.id == multi_task_id).first()

def create_multi_task(db: Session, user_id: int, structured_result_ids: List[int]) -> MultiTask:
    # Batch validate all structured results exist and belong to user
    srs = db.query(StructuredResult).filter(StructuredResult.id.in_(structured_result_ids)).all()
    sr_map = {sr.id: sr for sr in srs}

    missing = [sid for sid in structured_result_ids if sid not in sr_map]
    if missing:
        raise ResourceNotFoundError(f"StructuredResults not found: {missing}")

    # Batch fetch all OcrResults
    ocr_ids = [sr_map[sid].ocr_result_id for sid in structured_result_ids]
    ocrs = db.query(OcrResult).filter(OcrResult.id.in_(ocr_ids)).all()
    ocr_map = {ocr.id: ocr for ocr in ocrs}

    # Batch fetch all Images and validate ownership
    image_ids = [ocr_map[sr_map[sid].ocr_result_id].image_id for sid in structured_result_ids]
    images = db.query(Image).filter(Image.id.in_(image_ids)).all()
    image_map = {img.id: img for img in images}

    for sid in structured_result_ids:
        sr = sr_map[sid]
        ocr = ocr_map.get(sr.ocr_result_id)
        if not ocr:
            raise ResourceNotFoundError(f"OcrResult for StructuredResult {sid} not found")
        image = image_map.get(ocr.image_id)
        if not image or image.user_id != user_id:
            raise PermissionDeniedError(f"StructuredResult {sid} does not belong to the current user")

    try:
        multi_task = MultiTask(
            user_id=user_id,
            created_at=get_beijing_time()
        )
        db.add(multi_task)
        db.commit()
        db.refresh(multi_task)
        
        # Create associations
        for sr_id in structured_result_ids:
            association = MultiTaskStructuredResult(
                multi_task_id=multi_task.id,
                structured_result_id=sr_id,
                created_at=get_beijing_time()
            )
            db.add(association)
        
        db.commit()
        return multi_task
    except Exception as e:
        db.rollback()
        raise MultiTaskServiceError(f"Failed to create multi task: {str(e)}")

def delete_multi_task(db: Session, multi_task_id: int, user_id: int) -> dict:
    multi_task = get_multi_task(db, multi_task_id)
    if not multi_task:
        raise ResourceNotFoundError("MultiTask not found")

    if multi_task.user_id != user_id:
        raise PermissionDeniedError("Permission denied")

    # Count for stats
    deleted_multi_relation_count = db.query(MultiRelationGraph).filter(MultiRelationGraph.multi_task_id == multi_task_id).count()
    deleted_association_count = db.query(MultiTaskStructuredResult).filter(MultiTaskStructuredResult.multi_task_id == multi_task_id).count()

    try:
        # Cascade delete should handle children if configured, but we do explicit delete in main.py so let's mimic or rely on cascade
        # Assuming cascade is configured in models or we trust SQLAlchemy
        db.delete(multi_task)
        db.commit()
    except Exception as e:
        db.rollback()
        raise MultiTaskServiceError(f"Failed to delete multi task: {str(e)}")

    return {
        "multi_task_id": multi_task_id,
        "multi_relation_graphs": deleted_multi_relation_count,
        "multi_task_associations": deleted_association_count
    }

def find_latest_structured_results_for_images(db: Session, image_ids: List[int], user_id: Optional[int] = None) -> List[int]:
    structured_result_ids = []
    for image_id in image_ids:
        image = db.query(Image).filter(Image.id == image_id).first()
        if not image:
            raise ResourceNotFoundError(f"Image {image_id} not found")
        if user_id is not None and image.user_id != user_id:
            raise PermissionDeniedError(f"Image {image_id} does not belong to the current user")
        
        latest_ocr = (
            db.query(OcrResult)
            .filter(OcrResult.image_id == image_id)
            .order_by(OcrResult.id.desc())
            .first()
        )
        if not latest_ocr:
            raise ResourceNotFoundError(f"Image {image_id} has no OCR results")
        
        latest_structured = (
            db.query(StructuredResult)
            .filter(StructuredResult.ocr_result_id == latest_ocr.id)
            .order_by(StructuredResult.id.desc())
            .first()
        )
        if not latest_structured:
            raise ResourceNotFoundError(f"Image {image_id} has no structured results")
        
        structured_result_ids.append(latest_structured.id)
    return structured_result_ids

def get_multi_task_relation_ids(db: Session, multi_task_id: int, skip: int, limit: int) -> Tuple[List[int], int]:
    query = db.query(MultiRelationGraph.id).filter(MultiRelationGraph.multi_task_id == multi_task_id)
    total = query.count()
    ids = query.order_by(MultiRelationGraph.id.desc()).offset(skip).limit(limit).all()
    return [i[0] for i in ids], total

def get_multi_task_structured_result_ids(db: Session, multi_task_id: int) -> List[int]:
    associations = db.query(MultiTaskStructuredResult).filter(MultiTaskStructuredResult.multi_task_id == multi_task_id).all()
    return [assoc.structured_result_id for assoc in associations]

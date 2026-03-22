"""关系图路由：触发生成 / 获取单文档关系图 / 获取跨文档关系图"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Image, OcrResult, MultiRelationGraph, RelationGraph, StructuredResult, MultiTask, get_db
from app.core.deps import get_current_user_id
from app.core.logger import get_logger
from app.worker.tasks import task_analyze_structured_result, task_analyze_multi_task

logger = get_logger(__name__)
router = APIRouter(tags=["关系图"])


class CreateRelationGraphRequest(BaseModel):
    structured_result_id: int


class CreateMultiRelationGraphRequest(BaseModel):
    multi_task_id: int


# ── 单文档关系图 ──────────────────────────────────────────────

relation_graph_router = APIRouter(prefix="/api/v1/relation-graphs")


@relation_graph_router.post("", summary="触发单文档关系图生成", description="对指定结构化结果生成 ECharts 格式知识图谱（Celery 异步执行）")
async def create_relation_graph(
    request: CreateRelationGraphRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    structured_result = (
        db.query(StructuredResult)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(StructuredResult.id == request.structured_result_id, Image.user_id == user_id)
        .first()
    )
    if not structured_result:
        raise HTTPException(status_code=404, detail="StructuredResult不存在")

    task_analyze_structured_result.delay(request.structured_result_id)
    logger.info("relation_graph_triggered", extra={"structured_result_id": request.structured_result_id})

    return {
        "success": True,
        "message": f"StructuredResult {request.structured_result_id} 的关系图生成任务已提交到队列",
    }


@relation_graph_router.get("/{relation_graph_id}", summary="获取单文档关系图", description="返回指定关系图的 ECharts 节点/边数据（content字段），status 为 done 时可渲染")
async def get_relation_graph(
    relation_graph_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    relation_graph = (
        db.query(RelationGraph)
        .join(StructuredResult, RelationGraph.structured_result_id == StructuredResult.id)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(RelationGraph.id == relation_graph_id, Image.user_id == user_id)
        .first()
    )
    if not relation_graph:
        raise HTTPException(status_code=404, detail="RelationGraph不存在")

    try:
        content = json.loads(relation_graph.content)
    except Exception:
        content = relation_graph.content

    return {
        "success": True,
        "data": {
            "id": relation_graph.id,
            "structured_result_id": relation_graph.structured_result_id,
            "content": content,
            "status": relation_graph.status.value,
            "created_at": relation_graph.created_at.isoformat(),
        },
    }


# ── 跨文档关系图 ──────────────────────────────────────────────

multi_relation_graph_router = APIRouter(prefix="/api/v1/multi-relation-graphs")


@multi_relation_graph_router.post("", summary="触发跨文档关系图生成", description="对指定跨文档任务（MultiTask）执行实体消歧 + 多图合并，生成跨文档知识图谱（Celery 异步执行）")
async def create_multi_relation_graph(
    request: CreateMultiRelationGraphRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    multi_task = db.query(MultiTask).filter(MultiTask.id == request.multi_task_id, MultiTask.user_id == user_id).first()
    if not multi_task:
        raise HTTPException(status_code=404, detail="MultiTask不存在")

    task_analyze_multi_task.delay(request.multi_task_id)
    logger.info("multi_graph_triggered", extra={"multi_task_id": request.multi_task_id})

    return {
        "success": True,
        "message": f"MultiTask {request.multi_task_id} 的跨文档分析任务已提交到队列",
    }


@multi_relation_graph_router.get("/{multi_relation_graph_id}", summary="获取跨文档关系图", description="返回跨文档合并知识图谱数据，节点包含原始文书溯源信息")
async def get_multi_relation_graph(
    multi_relation_graph_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    multi_relation_graph = (
        db.query(MultiRelationGraph)
        .join(MultiTask, MultiRelationGraph.multi_task_id == MultiTask.id)
        .filter(MultiRelationGraph.id == multi_relation_graph_id, MultiTask.user_id == user_id)
        .first()
    )
    if not multi_relation_graph:
        raise HTTPException(status_code=404, detail="MultiRelationGraph不存在")

    try:
        content = json.loads(multi_relation_graph.content)
    except Exception:
        content = multi_relation_graph.content

    return {
        "success": True,
        "data": {
            "id": multi_relation_graph.id,
            "multi_task_id": multi_relation_graph.multi_task_id,
            "content": content,
            "status": multi_relation_graph.status.value,
            "created_at": multi_relation_graph.created_at.isoformat(),
        },
    }

"""跨文档任务状态、幂等性和输入过滤。"""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import (
    Base,
    Image,
    MultiRelationGraph,
    MultiTask,
    MultiTaskStructuredResult,
    OcrResult,
    OcrStatus,
    StructuredResult,
    User,
)
from app.services import analysis_service
from app.services.multi_task_service import find_latest_structured_results_for_images


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _create_user_and_image(db, username="owner"):
    user = User(username=username, password_hash="hashed")
    db.add(user)
    db.flush()
    image = Image(user_id=user.id, filename=f"{username}.jpg", path=f"/tmp/{username}.jpg")
    db.add(image)
    db.commit()
    return user, image


def test_find_latest_structured_results_only_uses_done_records(db):
    """最新记录失败时，应回退到最新已完成的结构化结果。"""
    user, image = _create_user_and_image(db)

    done_ocr = OcrResult(
        image_id=image.id,
        raw_text="done document",
        status=OcrStatus.DONE,
    )
    failed_ocr = OcrResult(
        image_id=image.id,
        raw_text="failed document",
        status=OcrStatus.FAILED,
    )
    db.add_all([done_ocr, failed_ocr])
    db.flush()

    done_structured = StructuredResult(
        ocr_result_id=done_ocr.id,
        content=json.dumps({"Seller": "张三"}),
        status=OcrStatus.DONE,
    )
    failed_structured = StructuredResult(
        ocr_result_id=failed_ocr.id,
        content=json.dumps({"error": "failed"}),
        status=OcrStatus.FAILED,
    )
    db.add_all([done_structured, failed_structured])
    db.commit()

    result = find_latest_structured_results_for_images(db, [image.id], user_id=user.id)

    assert result == [done_structured.id]


def test_analyze_multi_task_is_idempotent_and_updates_status(
    db,
    monkeypatch,
):
    """重复触发跨文档分析时复用图谱，并把任务状态置为 done。"""
    async def fake_insights(_statistics, _parsed_datas):
        return "测试洞察"

    monkeypatch.setattr(analysis_service, "call_llm_for_insights", fake_insights)

    user, first_image = _create_user_and_image(db, "first")
    second_user, second_image = _create_user_and_image(db, "second")
    assert second_user.id != user.id

    structured_ids = []
    for image in (first_image, second_image):
        ocr = OcrResult(
            image_id=image.id,
            raw_text=f"document {image.id}",
            status=OcrStatus.DONE,
        )
        db.add(ocr)
        db.flush()
        structured = StructuredResult(
            ocr_result_id=ocr.id,
            content=json.dumps(
                {
                    "Time": "道光元年",
                    "Time_AD": 1821,
                    "Location": "武昌府",
                    "Seller": "张三",
                    "Buyer": "李四",
                    "Middleman": "王五",
                    "Price": "纹银十两",
                    "Subject": "旱地一亩",
                },
                ensure_ascii=False,
            ),
            status=OcrStatus.DONE,
        )
        db.add(structured)
        db.flush()
        structured_ids.append(structured.id)

    task = MultiTask(user_id=user.id, status=OcrStatus.PROCESSING)
    db.add(task)
    db.flush()
    for structured_id in structured_ids:
        db.add(
            MultiTaskStructuredResult(
                multi_task_id=task.id,
                structured_result_id=structured_id,
            )
        )
    db.commit()

    asyncio.run(analysis_service.analyze_multi_task(task.id, db))
    first_graph_count = db.query(MultiRelationGraph).count()
    first_status = db.get(MultiTask, task.id).status

    asyncio.run(analysis_service.analyze_multi_task(task.id, db))
    graph_count = db.query(MultiRelationGraph).count()
    status = db.get(MultiTask, task.id).status

    assert first_graph_count == 1
    assert first_status == OcrStatus.DONE
    assert graph_count == 1
    assert status == OcrStatus.DONE

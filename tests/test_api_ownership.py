"""API 资源必须按用户隔离。"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from database import (
    Base,
    Image,
    MultiRelationGraph,
    MultiTask,
    OcrResult,
    RelationGraph,
    StructuredResult,
    User,
    get_db,
)
from main import app


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


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _token(user_id: int):
    return create_access_token(
        {"sub": f"user-{user_id}", "user_id": user_id},
    )


def _seed_owned_resources(db):
    owner = User(username="owner", password_hash="hash")
    intruder = User(username="intruder", password_hash="hash")
    db.add_all([owner, intruder])
    db.flush()

    image = Image(
        user_id=owner.id,
        filename="owner.jpg",
        path="/tmp/owner.jpg",
    )
    db.add(image)
    db.flush()
    ocr = OcrResult(
        image_id=image.id,
        raw_text="owner document",
        status="done",
    )
    db.add(ocr)
    db.flush()
    structured = StructuredResult(
        ocr_result_id=ocr.id,
        content=json.dumps({"Seller": "张三"}),
        status="done",
    )
    db.add(structured)
    db.flush()
    graph = RelationGraph(
        structured_result_id=structured.id,
        content=json.dumps({"series": []}),
        status="done",
    )
    db.add(graph)
    db.flush()
    task = MultiTask(user_id=owner.id, status="done")
    db.add(task)
    db.flush()
    multi_graph = MultiRelationGraph(
        multi_task_id=task.id,
        content=json.dumps({"series": []}),
        status="done",
    )
    db.add(multi_graph)
    db.commit()

    return {
        "owner": owner,
        "intruder": intruder,
        "image": image,
        "ocr": ocr,
        "structured": structured,
        "graph": graph,
        "multi_graph": multi_graph,
    }


def test_resources_are_invisible_to_other_users(client, db):
    ids = _seed_owned_resources(db)
    owner_token = _token(ids["owner"].id)
    intruder_token = _token(ids["intruder"].id)

    owner_cases = [
        f"/api/v1/images/{ids['image'].id}/info",
        f"/api/v1/ocr-results/{ids['ocr'].id}",
        f"/api/v1/structured-results/{ids['structured'].id}",
        f"/api/v1/relation-graphs/{ids['graph'].id}",
        f"/api/v1/multi-relation-graphs/{ids['multi_graph'].id}",
    ]
    for url in owner_cases:
        response = client.get(url, headers={"Authorization": f"Bearer {owner_token}"})
        assert response.status_code == 200, f"owner cannot access {url}: {response.text}"

    intruder_cases = [
        f"/api/v1/images/{ids['image'].id}/info",
        f"/api/v1/ocr-results/{ids['ocr'].id}",
        f"/api/v1/structured-results/{ids['structured'].id}",
        f"/api/v1/relation-graphs/{ids['graph'].id}",
        f"/api/v1/multi-relation-graphs/{ids['multi_graph'].id}",
    ]
    for url in intruder_cases:
        response = client.get(url, headers={"Authorization": f"Bearer {intruder_token}"})
        assert response.status_code == 404, f"intruder can access {url}: {response.text}"


def test_user_image_list_only_contains_owned_images(client, db):
    ids = _seed_owned_resources(db)
    second_image = Image(
        user_id=ids["intruder"].id,
        filename="intruder.jpg",
        path="/tmp/intruder.jpg",
    )
    db.add(second_image)
    db.commit()

    response = client.get(
        "/api/v1/users/images?limit=100",
        headers={"Authorization": f"Bearer {_token(ids['owner'].id)}"},
    )

    assert response.status_code == 200
    returned = response.json()["data"]["items"]
    assert [item["id"] for item in returned] == [ids["image"].id]

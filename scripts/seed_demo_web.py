#!/usr/bin/env python3
"""Seed a ready-to-demo web account from existing completed analysis data."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from app.core.security import hash_password
from database import (
    Image,
    OcrResult,
    OcrStatus,
    RelationGraph,
    SessionLocal,
    StructuredResult,
    User,
    get_beijing_time,
)


DEMO_USERNAME = "demo_web"
DEMO_PASSWORD = "DemoWeb2026!"
DEMO_EMAIL = "demo-web@example.local"
DEMO_PREFIX = "demo_web_src_"


@dataclass(frozen=True)
class DemoSeedSummary:
    username: str
    password: str
    created_user: bool
    copied_images: int
    existing_images: int


@dataclass(frozen=True)
class SourceBundle:
    image: Image
    ocr_result: OcrResult
    structured_result: StructuredResult
    relation_graph: RelationGraph


def _demo_filename(source_image_id: int, source_filename: str) -> str:
    clean = os.path.basename(source_filename).replace(os.sep, "_")
    return f"{DEMO_PREFIX}{source_image_id}_{clean}"


def _ensure_demo_user(
    db: Session,
    username: str,
    password: str,
    email: str,
) -> tuple[User, bool]:
    user = db.query(User).filter(User.username == username).first()
    if user:
        return user, False

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        created_at=get_beijing_time(),
    )
    db.add(user)
    db.flush()
    return user, True


def _completed_source_bundles(
    db: Session,
    source_user_id: int,
    limit: int,
) -> list[SourceBundle]:
    rows = (
        db.query(Image, OcrResult, StructuredResult, RelationGraph)
        .join(OcrResult, OcrResult.image_id == Image.id)
        .join(StructuredResult, StructuredResult.ocr_result_id == OcrResult.id)
        .join(RelationGraph, RelationGraph.structured_result_id == StructuredResult.id)
        .filter(
            Image.user_id == source_user_id,
            OcrResult.status == OcrStatus.DONE,
            StructuredResult.status == OcrStatus.DONE,
            RelationGraph.status == OcrStatus.DONE,
        )
        .order_by(Image.upload_time.desc(), StructuredResult.id.desc(), RelationGraph.id.desc())
        .all()
    )

    bundles: list[SourceBundle] = []
    seen_image_ids: set[int] = set()
    for image, ocr_result, structured_result, relation_graph in rows:
        if image.id in seen_image_ids:
            continue
        seen_image_ids.add(image.id)
        bundles.append(SourceBundle(image, ocr_result, structured_result, relation_graph))
        if len(bundles) >= limit:
            break
    return bundles


def _copy_file(source_path: str, target_path: str) -> None:
    source = Path(source_path)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists() and not target.exists():
        shutil.copy2(source, target)


def _copy_bundle(
    db: Session,
    demo_user: User,
    bundle: SourceBundle,
    upload_dir: str,
) -> bool:
    if not Path(bundle.image.path).exists():
        return False

    filename = _demo_filename(bundle.image.id, bundle.image.filename)
    existing = (
        db.query(Image)
        .filter(Image.user_id == demo_user.id, Image.filename == filename)
        .first()
    )
    if existing:
        return False

    target_path = os.path.join(upload_dir, filename)
    _copy_file(bundle.image.path, target_path)

    now = get_beijing_time()
    image = Image(
        user_id=demo_user.id,
        filename=filename,
        path=target_path,
        upload_time=now,
    )
    db.add(image)
    db.flush()

    ocr_result = OcrResult(
        image_id=image.id,
        raw_text=bundle.ocr_result.raw_text,
        status=bundle.ocr_result.status,
        confidence=bundle.ocr_result.confidence,
        coverage=bundle.ocr_result.coverage,
        engine=bundle.ocr_result.engine,
        model_versions=bundle.ocr_result.model_versions,
        original_raw_text=bundle.ocr_result.original_raw_text,
        segments_json=bundle.ocr_result.segments_json,
        corrected_segments_json=bundle.ocr_result.corrected_segments_json,
        correction_metadata_json=bundle.ocr_result.correction_metadata_json,
        rejection_reasons=bundle.ocr_result.rejection_reasons,
        crop_bbox_json=bundle.ocr_result.crop_bbox_json,
        image_size_json=bundle.ocr_result.image_size_json,
        human_corrected=bundle.ocr_result.human_corrected,
        created_at=now,
    )
    db.add(ocr_result)
    db.flush()

    structured_result = StructuredResult(
        ocr_result_id=ocr_result.id,
        content=bundle.structured_result.content,
        status=bundle.structured_result.status,
        created_at=now,
    )
    db.add(structured_result)
    db.flush()

    relation_graph = RelationGraph(
        structured_result_id=structured_result.id,
        content=bundle.relation_graph.content,
        status=bundle.relation_graph.status,
        created_at=now,
    )
    db.add(relation_graph)
    return True


def seed_demo_web(
    db: Session,
    *,
    source_user_id: int = 1,
    username: str = DEMO_USERNAME,
    password: str = DEMO_PASSWORD,
    email: str = DEMO_EMAIL,
    limit: int = 12,
    upload_dir: str = "pic",
) -> DemoSeedSummary:
    demo_user, created_user = _ensure_demo_user(db, username, password, email)
    bundles = _completed_source_bundles(db, source_user_id, limit)
    copied = 0
    existing = 0

    for bundle in bundles:
        if _copy_bundle(db, demo_user, bundle, upload_dir):
            copied += 1
        else:
            existing += 1

    db.commit()
    return DemoSeedSummary(
        username=username,
        password=password,
        created_user=created_user,
        copied_images=copied,
        existing_images=existing,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the web demo account.")
    parser.add_argument("--source-user-id", type=int, default=1)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--username", default=DEMO_USERNAME)
    parser.add_argument("--password", default=DEMO_PASSWORD)
    parser.add_argument("--email", default=DEMO_EMAIL)
    parser.add_argument("--upload-dir", default="pic")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = seed_demo_web(
            db,
            source_user_id=args.source_user_id,
            username=args.username,
            password=args.password,
            email=args.email,
            limit=args.limit,
            upload_dir=args.upload_dir,
        )
    finally:
        db.close()

    print(
        "Demo account ready: "
        f"{summary.username} / {summary.password}; "
        f"copied={summary.copied_images}; existing={summary.existing_images}; "
        f"created_user={summary.created_user}"
    )


if __name__ == "__main__":
    main()

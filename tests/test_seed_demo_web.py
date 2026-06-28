from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import (
    Base,
    Image,
    OcrResult,
    OcrStatus,
    RelationGraph,
    StructuredResult,
    User,
    get_beijing_time,
)
from scripts.seed_demo_web import DEMO_PREFIX, seed_demo_web


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _source_bundle(db, tmp_path: Path):
    source_file = tmp_path / "source.jpg"
    source_file.write_bytes(b"demo image bytes")

    user = User(
        username="source",
        email="source@example.local",
        password_hash="hash",
        created_at=get_beijing_time(),
    )
    db.add(user)
    db.flush()

    image = Image(
        user_id=user.id,
        filename="contract.jpg",
        path=str(source_file),
        upload_time=get_beijing_time(),
    )
    db.add(image)
    db.flush()

    ocr = OcrResult(
        image_id=image.id,
        raw_text="立永賣白田約人",
        status=OcrStatus.DONE,
        confidence=0.8,
        coverage=0.7,
        engine="test_engine",
        model_versions="test_model",
        original_raw_text="立永賣白田約人",
        segments_json='[{"segment_id":"s0000","text":"立永賣","image_bbox":[1,2,3,4]}]',
        corrected_segments_json='[{"segment_id":"s0000","text":"立永賣"}]',
        correction_metadata_json='{"mode":"segments"}',
        rejection_reasons='["masked:model_disagreement"]',
        crop_bbox_json="[0,0,800,600]",
        image_size_json="[800,600]",
        human_corrected=False,
        created_at=get_beijing_time(),
    )
    db.add(ocr)
    db.flush()

    structured = StructuredResult(
        ocr_result_id=ocr.id,
        content='{"Seller":"熊某","Buyer":"篤敘堂"}',
        status=OcrStatus.DONE,
        created_at=get_beijing_time(),
    )
    db.add(structured)
    db.flush()

    graph = RelationGraph(
        structured_result_id=structured.id,
        content='{"series":[{"type":"graph","data":[]}]}',
        status=OcrStatus.DONE,
        created_at=get_beijing_time(),
    )
    db.add(graph)
    db.commit()
    return user, image


def test_seed_demo_web_is_idempotent_and_copies_files(tmp_path):
    db = _session()
    try:
        source_user, source_image = _source_bundle(db, tmp_path)
        upload_dir = tmp_path / "demo_pic"

        first = seed_demo_web(
            db,
            source_user_id=source_user.id,
            username="demo_web_test",
            password="DemoWeb2026!",
            limit=12,
            upload_dir=str(upload_dir),
        )
        second = seed_demo_web(
            db,
            source_user_id=source_user.id,
            username="demo_web_test",
            password="DemoWeb2026!",
            limit=12,
            upload_dir=str(upload_dir),
        )

        demo_user = db.query(User).filter(User.username == "demo_web_test").one()
        demo_images = db.query(Image).filter(Image.user_id == demo_user.id).all()

        assert first.created_user is True
        assert first.copied_images == 1
        assert second.created_user is False
        assert second.copied_images == 0
        assert second.existing_images == 1
        assert len(demo_images) == 1
        assert demo_images[0].filename.startswith(DEMO_PREFIX)
        assert demo_images[0].path != source_image.path
        assert Path(demo_images[0].path).read_bytes() == b"demo image bytes"
        copied_ocr = db.query(OcrResult).filter(OcrResult.image_id == demo_images[0].id).one()
        assert copied_ocr.original_raw_text == "立永賣白田約人"
        assert copied_ocr.corrected_segments_json == '[{"segment_id":"s0000","text":"立永賣"}]'
        assert copied_ocr.crop_bbox_json == "[0,0,800,600]"
        assert copied_ocr.image_size_json == "[800,600]"
    finally:
        db.close()


def test_seed_demo_web_skips_missing_source_file(tmp_path):
    db = _session()
    try:
        source_user, source_image = _source_bundle(db, tmp_path)
        Path(source_image.path).unlink()

        summary = seed_demo_web(
            db,
            source_user_id=source_user.id,
            username="demo_web_missing_file",
            password="DemoWeb2026!",
            limit=12,
            upload_dir=str(tmp_path / "demo_pic"),
        )

        demo_user = db.query(User).filter(User.username == "demo_web_missing_file").one()
        assert summary.copied_images == 0
        assert db.query(Image).filter(Image.user_id == demo_user.id).count() == 0
    finally:
        db.close()

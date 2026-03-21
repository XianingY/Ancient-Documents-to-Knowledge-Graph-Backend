"""
统计分析路由
提供古籍文书的数据统计聚合接口，供前端看板使用
"""
import json
from collections import Counter
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import Image, OcrResult, StructuredResult, User, get_db
from app.core.logger import get_logger
from app.core.security import security, verify_token

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/statistics", tags=["数据统计"])


def _parse_structured_results(db: Session, user_id: int) -> List[Dict[str, Any]]:
    """查询当前用户所有已完成的结构化结果并解析 JSON"""
    rows = (
        db.query(StructuredResult.content)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(Image.user_id == user_id, StructuredResult.status == "done")
        .all()
    )
    results = []
    for (content,) in rows:
        try:
            data = json.loads(content)
            results.append(data)
        except Exception:
            pass
    return results


@router.get(
    "",
    summary="获取数据统计看板",
    description="聚合当前用户所有已分析文书的统计数据：文书总数、分析完成数、时间年代分布、地点TOP10、高频人物TOP10、历史地价趋势",
)
async def get_statistics(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """
    返回当前用户文书库的综合统计数据：
    - 文书总数 / 已完成分析数
    - 时间分布（公元年份）
    - 地点 Top10
    - 价格趋势（按年份分组）
    - 人物出现频次 Top10（卖方 / 买方 / 中人合并）
    """
    payload = verify_token(credentials.credentials)
    user_id = payload.get("user_id")

    # 文书总数
    total_images = db.query(Image).filter(Image.user_id == user_id).count()
    total_done = (
        db.query(StructuredResult)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .join(Image, OcrResult.image_id == Image.id)
        .filter(Image.user_id == user_id, StructuredResult.status == "done")
        .count()
    )

    parsed = _parse_structured_results(db, user_id)

    _EMPTY = {"未识别", "未知", "None", "none", ""}

    def _is_empty(val: Any) -> bool:
        return not val or str(val).strip() in _EMPTY

    # ── 时间分布 ──────────────────────────────────────────────
    year_counter: Counter = Counter()
    for d in parsed:
        try:
            y = int(d.get("Time_AD", ""))
            year_counter[y] += 1
        except (ValueError, TypeError):
            pass

    time_distribution = [
        {"year": y, "count": c}
        for y, c in sorted(year_counter.items())
    ]

    # ── 地点分布 Top10 ────────────────────────────────────────
    loc_counter: Counter = Counter()
    for d in parsed:
        loc = str(d.get("Location", "")).strip()
        if not _is_empty(loc):
            loc_counter[loc] += 1

    location_distribution = [
        {"name": loc, "count": cnt}
        for loc, cnt in loc_counter.most_common(10)
    ]

    # ── 人物频次 Top10 ────────────────────────────────────────
    people_counter: Counter = Counter()
    for d in parsed:
        for role_key in ("Seller", "Buyer", "Middleman"):
            name = str(d.get(role_key, "")).strip()
            if not _is_empty(name):
                people_counter[name] += 1

    top_people = [
        {"name": name, "count": cnt}
        for name, cnt in people_counter.most_common(10)
    ]

    # ── 价格趋势（按年份分组，仅提取数值部分）────────────────
    import re
    price_trend: Dict[int, List[float]] = {}
    for d in parsed:
        try:
            year = int(d.get("Time_AD", ""))
        except (ValueError, TypeError):
            continue
        price_str = str(d.get("Price", ""))
        numbers = re.findall(r"\d+(?:\.\d+)?", price_str)
        if numbers:
            val = float(numbers[0])
            price_trend.setdefault(year, []).append(val)

    price_trend_list = [
        {"year": y, "avg_price": round(sum(vals) / len(vals), 2), "count": len(vals)}
        for y, vals in sorted(price_trend.items())
    ]

    # ── 整体时间跨度 ──────────────────────────────────────────
    years = list(year_counter.keys())
    time_range = {}
    if years:
        time_range = {
            "start": min(years),
            "end": max(years),
            "span": max(years) - min(years),
        }

    logger.info("statistics_queried", extra={"user_id": user_id, "total_images": total_images})

    return {
        "success": True,
        "data": {
            "total_images": total_images,
            "total_analyzed": total_done,
            "time_range": time_range,
            "time_distribution": time_distribution,
            "location_distribution": location_distribution,
            "top_people": top_people,
            "price_trend": price_trend_list,
        },
    }

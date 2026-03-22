"""
分析服务编排层
负责将 OCR 结果 → 结构化提取 → 关系图生成 → 跨文档分析的完整流水线进行编排。
底层 LLM 调用委托给 llm_client.py，图谱构建委托给 graph_service.py。
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import networkx as nx
from sqlalchemy.orm import Session
from app.core.config import settings
from fastapi.concurrency import run_in_threadpool

from database import (
    StructuredResult, RelationGraph, MultiTask, MultiRelationGraph,
    OcrResult, OcrStatus, MultiTaskStructuredResult, get_beijing_time
)

# 从新拆分的子模块导入
from app.services.llm_client import (
    call_structure_llm as call_llm_for_structure,
    call_insights_llm as call_llm_for_insights,
    HAS_DASHSCOPE,
)
from app.services.graph_service import (
    build_graph_from_structure,
    analyze_structured_result,
)

async def analyze_ocr_result(ocr_result_id: int, db: Session) -> None:
    """
    对OcrResult进行结构化分析。
    与 OCR 流程保持一致：先立即写入 PROCESSING 状态，再执行 LLM，
    这样前端触发后立刻查询就能拿到记录 ID 并开始轮询。
    """
    ocr_result = db.query(OcrResult).filter(OcrResult.id == ocr_result_id).first()
    if not ocr_result:
        print(f"OcrResult {ocr_result_id} not found")
        return

    if not ocr_result.raw_text:
        print(f"OcrResult {ocr_result_id} has no text")
        return

    # ① 立即写入 PROCESSING 状态（与 OCR 保持一致，让前端能立即轮询到）
    structured_result = StructuredResult(
        ocr_result_id=ocr_result_id,
        content=json.dumps({}),
        status=OcrStatus.PROCESSING,
        created_at=get_beijing_time(),
    )
    db.add(structured_result)
    db.commit()
    db.refresh(structured_result)

    try:
        # ② 调用 LLM 结构化提取
        structured_data = await call_llm_for_structure(ocr_result.raw_text)

        # 补充文件名信息（用于 RAG 元数据）
        if ocr_result.image:
            structured_data["filename"] = ocr_result.image.filename

        # ③ 更新为 DONE 状态
        structured_result.content = json.dumps(structured_data, ensure_ascii=False)
        structured_result.status = OcrStatus.DONE
        db.commit()
        print(f"Structured analysis for {ocr_result_id} completed.")

        # ④ 用富文本覆盖 ChromaDB 向量索引
        # doc_id = image_{image_id}，与 OCR 阶段一致，upsert 覆盖基础版，补充结构化元数据
        # 富文本 = OCR 原文 + 结构化字段摘要，使"找买方是张三的契约"等语义查询更准确
        try:
            from app.services.rag_service import _get_text_embeddings_sync
            from app.services.vector_store.chroma import upsert_document

            _EMPTY = {"未识别", "未记载", "None", "null", ""}

            def _field(key: str) -> str:
                v = str(structured_data.get(key, "")).strip()
                return v if v not in _EMPTY else ""

            parts = [ocr_result.raw_text]
            fields = [
                ("时间",   _field("Time")),
                ("地点",   _field("Location")),
                ("卖方",   _field("Seller")),
                ("买方",   _field("Buyer")),
                ("中人",   _field("Middleman")),
                ("价格",   _field("Price")),
                ("标的",   _field("Subject")),
            ]
            filled = [f"【{k}】{v}" for k, v in fields if v]
            if filled:
                parts.append("\n" + "　".join(filled))
            rich_text = "\n".join(parts)

            image_id = ocr_result.image_id
            embedding = _get_text_embeddings_sync(rich_text)
            metadata = {
                "user_id": ocr_result.image.user_id if ocr_result.image else 0,
                "structured_result_id": structured_result.id,
                "ocr_result_id": ocr_result.id,
                "image_id": image_id,
                "filename": structured_data.get("filename", ""),
                "time": _field("Time"),
                "location": _field("Location"),
                "seller": _field("Seller"),
                "buyer": _field("Buyer"),
                "price": _field("Price"),
                "subject": _field("Subject"),
            }
            upsert_document(
                doc_id=f"image_{image_id}",
                text=rich_text,
                embedding=embedding,
                metadata=metadata,
            )
            print(f"Image {image_id} re-indexed with structured enrichment (doc_id=image_{image_id}).")
        except Exception as idx_err:
            print(f"ChromaDB enrichment indexing failed (non-fatal): {idx_err}")

    except Exception as e:
        print(f"Error analyzing OCR result {ocr_result_id}: {e}")
        structured_result.content = json.dumps({"error": str(e)})
        structured_result.status = OcrStatus.FAILED
        db.commit()


from app.services.analysis_components.entity_resolver import EntityResolver

# ─────────────────────────────────────────────────────────────
#  跨文档分析主函数（增强版）
# ─────────────────────────────────────────────────────────────

_EMPTY_VALS = {"未识别", "未知", "None", "none", ""}


def _is_empty(val: Any) -> bool:
    return not val or str(val).strip() in _EMPTY_VALS


async def analyze_multi_task(multi_task_id: int, db: Session) -> None:
    """
    对 MultiTask 进行跨文档分析（增强版）

    新增能力：
    1. 中文图例（卖方/买方/中人/地块/跨角色）
    2. 地产流转节点：同一地点 2+ 次交易时生成"地块"节点，连接各交易方
    3. 跨角色人物：在不同文书中身兼多职的人物以紫色菱形显示
    4. 统计数据：时间跨度、核心人物、主要地点、地产链
    5. LLM 历史洞察：调用 LLM 生成自然语言分析摘要
    """
    try:
        multi_task = db.query(MultiTask).filter(MultiTask.id == multi_task_id).first()
        if not multi_task:
            return

        associations = db.query(MultiTaskStructuredResult).filter(
            MultiTaskStructuredResult.multi_task_id == multi_task_id
        ).all()
        sr_ids = [a.structured_result_id for a in associations]
        structured_results = db.query(StructuredResult).filter(
            StructuredResult.id.in_(sr_ids)
        ).all()

        if not structured_results:
            print(f"No structured results for MultiTask {multi_task_id}")
            return

        # ── 解析所有文档数据 ──────────────────────────────────────
        parsed_datas: List[Dict] = []
        for sr in structured_results:
            try:
                parsed_datas.append(json.loads(sr.content))
            except json.JSONDecodeError:
                parsed_datas.append({})

        # ── CPU密集型图构建（threadpool中运行） ──────────────────
        def _build_merged_graph():
            from collections import Counter, defaultdict

            G = nx.Graph()

            # 1. 收集原始节点
            raw_nodes = []
            for sr, data in zip(structured_results, parsed_datas):
                doc_id = str(sr.id)
                time_ad = data.get("Time_AD")
                location = data.get("Location", "").strip()
                if location in _EMPTY_VALS:
                    location = ""
                for role in ["Seller", "Buyer", "Middleman"]:
                    name = str(data.get(role, "")).strip()
                    if name and name not in _EMPTY_VALS:
                        raw_nodes.append({
                            "original_name": name,
                            "role": role,
                            "doc_id": doc_id,
                            "time_ad": time_ad,
                            "location": location,
                            "data": data,
                        })

            # 2. 实体消歧
            merged_entities = EntityResolver.resolve_entities(raw_nodes)

            # 名称 → 实体的查找索引
            name_to_entity: Dict[str, Dict] = {}
            for entity in merged_entities:
                for inst in entity["instances"]:
                    name_to_entity[inst["original_name"]] = entity

            # 3. 统计信息计算
            location_counter: Counter = Counter()
            int_years: List[int] = []
            for data in parsed_datas:
                loc = str(data.get("Location", "")).strip()
                if loc and loc not in _EMPTY_VALS:
                    location_counter[loc] += 1
                try:
                    y = int(data.get("Time_AD", ""))
                    int_years.append(y)
                except (ValueError, TypeError):
                    pass

            time_range: Dict[str, Any] = {}
            if int_years:
                time_range = {
                    "start": min(int_years),
                    "end": max(int_years),
                    "span": max(int_years) - min(int_years),
                    "docs_with_time": len(int_years),
                }

            top_people = sorted(
                merged_entities,
                key=lambda e: len(set(i["doc_id"] for i in e["instances"])),
                reverse=True,
            )[:5]

            # 地产流转：出现 2+ 次的地点
            land_locations = {loc for loc, cnt in location_counter.items() if cnt >= 2}
            land_chains = []
            for loc in land_locations:
                docs_here = [
                    {"time_ad": d.get("Time_AD"), "seller": d.get("Seller", ""), "buyer": d.get("Buyer", "")}
                    for d in parsed_datas
                    if str(d.get("Location", "")).strip() == loc
                ]
                try:
                    years = sorted(
                        int(dd["time_ad"]) for dd in docs_here
                        if dd.get("time_ad") and str(dd["time_ad"]) not in _EMPTY_VALS
                    )
                except Exception:
                    years = []
                land_chains.append({
                    "location": loc,
                    "transaction_count": location_counter[loc],
                    "years": years,
                })

            statistics = {
                "doc_count": len(structured_results),
                "time_range": time_range,
                "unique_people": len(merged_entities),
                "cross_role_people": [
                    e["standard_name"] for e in merged_entities if e.get("cross_role")
                ],
                "top_people": [
                    {
                        "name": e["standard_name"],
                        "doc_count": len(set(i["doc_id"] for i in e["instances"])),
                        "roles": list(set(i["role"] for i in e["instances"])),
                    }
                    for e in top_people
                ],
                "top_locations": [
                    {"name": loc, "count": cnt}
                    for loc, cnt in location_counter.most_common(5)
                ],
                "land_chain_count": len(land_chains),
                "land_chains": land_chains[:5],
            }

            # 4. 构建 NetworkX 图
            # 类别：0=卖方 1=买方 2=中人 3=地块 4=跨角色
            CATEGORY_MAP = {"Seller": 0, "Buyer": 1, "Middleman": 2}
            ROLE_ZH = {"Seller": "卖方", "Buyer": "买方", "Middleman": "中人"}

            NODE_COLORS = {
                0: "#dc2626",   # 卖方 红
                1: "#2563eb",   # 买方 蓝
                2: "#059669",   # 中人 绿
                3: "#d97706",   # 地块 琥珀
                4: "#7c3aed",   # 跨角色 紫
            }
            NODE_BORDER = {
                0: "#fca5a5", 1: "#93c5fd", 2: "#6ee7b7",
                3: "#fcd34d", 4: "#c4b5fd",
            }

            cross_role_names = {e["standard_name"] for e in merged_entities if e.get("cross_role")}

            # 添加人物节点
            for entity in merged_entities:
                name = entity["standard_name"]
                doc_count_e = len(set(i["doc_id"] for i in entity["instances"]))
                is_cross = name in cross_role_names
                cat_idx = 4 if is_cross else CATEGORY_MAP.get(entity["role"], 0)
                roles_zh = "/".join(ROLE_ZH.get(r, r) for r in sorted(set(i["role"] for i in entity["instances"])))

                G.add_node(name,
                           category=cat_idx,
                           doc_count=doc_count_e,
                           roles_zh=roles_zh,
                           cross_role=is_cross)

            # 添加地块节点
            for loc in land_locations:
                land_id = f"[地块]{loc}"
                G.add_node(land_id, category=3, doc_count=location_counter[loc],
                           roles_zh="地块", cross_role=False)

            # 5. 添加边

            # 按文档逐条处理
            doc_entity_map: Dict[str, Dict[str, str]] = defaultdict(dict)
            for entity in merged_entities:
                for inst in entity["instances"]:
                    doc_entity_map[inst["doc_id"]][inst["role"]] = entity["standard_name"]

            for sr, data in zip(structured_results, parsed_datas):
                doc_id = str(sr.id)
                de = doc_entity_map.get(doc_id, {})
                seller = de.get("Seller")
                buyer = de.get("Buyer")
                middleman = de.get("Middleman")
                time_label = str(data.get("Time_AD", "")).strip()
                if time_label in _EMPTY_VALS:
                    time_label = data.get("Time", "").strip()
                if time_label in _EMPTY_VALS:
                    time_label = ""

                # 买卖关系
                if seller and buyer and G.has_node(seller) and G.has_node(buyer):
                    edge_label = f"出售{'·' + time_label if time_label else ''}"
                    if G.has_edge(seller, buyer):
                        G[seller][buyer]["doc_ids"] = G[seller][buyer].get("doc_ids", []) + [doc_id]
                        G[seller][buyer]["count"] = G[seller][buyer].get("count", 1) + 1
                    else:
                        G.add_edge(seller, buyer, relation="Trade",
                                   label=edge_label, doc_ids=[doc_id], count=1)

                # 见证关系
                for party in [seller, buyer]:
                    if middleman and party and G.has_node(middleman) and G.has_node(party):
                        if G.has_edge(middleman, party):
                            G[middleman][party]["count"] = G[middleman][party].get("count", 1) + 1
                        else:
                            G.add_edge(middleman, party, relation="Witness",
                                       label="见证", doc_ids=[doc_id], count=1)

                # 地产流转：连接各方与地块节点
                loc = str(data.get("Location", "")).strip()
                if loc and loc not in _EMPTY_VALS and loc in land_locations:
                    land_id = f"[地块]{loc}"
                    for party in [seller, buyer]:
                        if party and G.has_node(party) and G.has_node(land_id):
                            edge_label = f"{'出让' if party == seller else '受让'}{'·' + time_label if time_label else ''}"
                            if not G.has_edge(land_id, party):
                                G.add_edge(land_id, party, relation="LandChange",
                                           label=edge_label, doc_ids=[doc_id], count=1)

            # 6. 转换为 ECharts force-layout 格式
            try:
                degree_centrality = nx.degree_centrality(G)
            except Exception:
                degree_centrality = {}

            categories = [
                {"name": "卖方"},
                {"name": "买方"},
                {"name": "中人"},
                {"name": "地块"},
                {"name": "跨角色"},
            ]

            echarts_nodes = []
            for node in G.nodes():
                attrs = G.nodes[node]
                cat_idx = attrs.get("category", 0)
                doc_cnt = attrs.get("doc_count", 1)
                centrality = degree_centrality.get(node, 0)
                is_land = cat_idx == 3

                base_size = 38 if is_land else 28
                size = min(80, base_size + centrality * 55 + doc_cnt * 6)

                color = NODE_COLORS.get(cat_idx, "#6b7280")
                border = NODE_BORDER.get(cat_idx, "#d1d5db")
                symbol = "roundRect" if is_land else ("diamond" if cat_idx == 4 else "circle")

                display_name = node.replace("[地块]", "") if is_land else node
                roles_zh = attrs.get("roles_zh", "")
                tooltip_text = (
                    f"地块：{display_name}<br/>交易次数：{doc_cnt}"
                    if is_land
                    else f"{display_name}<br/>角色：{roles_zh}<br/>涉及文书：{doc_cnt} 份"
                )

                echarts_nodes.append({
                    "name": node,
                    "category": cat_idx,
                    "symbolSize": size,
                    "symbol": symbol,
                    "value": tooltip_text,
                    "label": {
                        "show": True,
                        "formatter": display_name,
                        "position": "bottom",
                        "fontSize": 12,
                        "fontWeight": "bold" if doc_cnt >= 2 else "normal",
                        "color": color,
                    },
                    "itemStyle": {
                        "color": color,
                        "borderColor": border,
                        "borderWidth": 2,
                        "opacity": 1.0,
                    },
                })

            RELATION_STYLES = {
                "Trade": {
                    "color": "#1e40af",
                    "width": 3,
                    "type": "solid",
                    "opacity": 0.85,
                },
                "Witness": {
                    "color": "#059669",
                    "width": 2,
                    "type": "dashed",
                    "opacity": 0.7,
                },
                "LandChange": {
                    "color": "#b45309",
                    "width": 2.5,
                    "type": "solid",
                    "opacity": 0.8,
                },
            }

            echarts_links = []
            for u, v, edata in G.edges(data=True):
                rel = edata.get("relation", "Trade")
                style = RELATION_STYLES.get(rel, RELATION_STYLES["Trade"])
                count = edata.get("count", 1)
                lbl = edata.get("label", rel)
                if count > 1:
                    lbl = f"{lbl}(×{count})"

                echarts_links.append({
                    "source": u,
                    "target": v,
                    "value": lbl,
                    "label": {
                        "show": True,
                        "formatter": lbl,
                        "fontSize": 10,
                        "fontWeight": "bold",
                        "backgroundColor": "rgba(255,255,255,0.7)",
                        "borderRadius": 3,
                        "padding": [2, 4],
                    },
                    "lineStyle": {
                        "color": style["color"],
                        "width": style["width"] + (count - 1) * 0.5,
                        "type": style["type"],
                        "opacity": style["opacity"],
                    },
                })

            echarts_option = {
                "tooltip": {"trigger": "item", "formatter": "{b}<br/>{c}"},
                "legend": [
                    {
                        "data": ["卖方", "买方", "中人", "地块", "跨角色"],
                        "bottom": 4,
                        "textStyle": {"fontSize": 11},
                        "itemWidth": 12,
                        "itemHeight": 12,
                        "icon": "circle",
                    }
                ],
                "series": [
                    {
                        "type": "graph",
                        "layout": "force",
                        "categories": categories,
                        "data": echarts_nodes,
                        "links": echarts_links,
                        "roam": True,
                        "label": {"position": "bottom", "formatter": "{b}"},
                        "force": {
                            "repulsion": 800,
                            "edgeLength": [80, 220],
                            "gravity": 0.1,
                            "layoutAnimation": True,
                            "friction": 0.6,
                        },
                        "emphasis": {
                            "focus": "adjacency",
                            "lineStyle": {"width": 5},
                            "label": {"show": True, "fontWeight": "bold"},
                            "itemStyle": {"shadowBlur": 12, "shadowColor": "rgba(0,0,0,0.35)"},
                        },
                        "blur": {
                            "itemStyle": {"opacity": 0.2},
                            "lineStyle": {"opacity": 0.1},
                            "label": {"opacity": 0.25},
                        },
                    }
                ],
                # 统计数据与洞察（前端额外展示）
                "statistics": statistics,
            }

            return echarts_option, statistics, parsed_datas

        # ── 在 threadpool 中运行图构建 ──────────────────────────
        echarts_option, statistics, parsed_datas_out = await run_in_threadpool(_build_merged_graph)

        # ── 调用 LLM 生成历史洞察 ───────────────────────────────
        insights = await call_llm_for_insights(statistics, parsed_datas_out)
        echarts_option["insights"] = insights

        # ── 持久化 ──────────────────────────────────────────────
        multi_relation_graph = MultiRelationGraph(
            multi_task_id=multi_task_id,
            content=json.dumps(echarts_option, ensure_ascii=False),
            status=OcrStatus.DONE,
            created_at=get_beijing_time(),
        )
        db.add(multi_relation_graph)
        db.commit()
        print(f"Multi-task analysis for {multi_task_id} completed (enhanced).")

    except Exception as e:
        print(f"Error analyzing multi task {multi_task_id}: {str(e)}")
        multi_relation_graph = MultiRelationGraph(
            multi_task_id=multi_task_id,
            content=json.dumps({"error": str(e)}),
            status=OcrStatus.FAILED,
            created_at=get_beijing_time(),
        )
        db.add(multi_relation_graph)
        db.commit()


# ── 同步包装器（供 Celery Worker 调用，避免 asyncio.run() 冲突）──────────────

def analyze_ocr_result_sync(ocr_result_id: int, db) -> None:
    """analyze_ocr_result 的同步包装，在 Celery Worker 中直接调用"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(analyze_ocr_result(ocr_result_id, db))
    finally:
        loop.close()


def analyze_structured_result_sync(structured_result_id: int, db) -> None:
    """analyze_structured_result 的同步包装，在 Celery Worker 中直接调用"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(analyze_structured_result(structured_result_id, db))
    finally:
        loop.close()


def analyze_multi_task_sync(multi_task_id: int, db) -> None:
    """analyze_multi_task 的同步包装，在 Celery Worker 中直接调用"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(analyze_multi_task(multi_task_id, db))
    finally:
        loop.close()

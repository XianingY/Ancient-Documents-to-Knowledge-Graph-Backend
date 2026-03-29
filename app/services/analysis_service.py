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

    # ① 复用已有记录或新建（避免同一 OCR 多次刷新产生多条记录）
    existing = (
        db.query(StructuredResult)
        .filter(StructuredResult.ocr_result_id == ocr_result_id)
        .order_by(StructuredResult.id.desc())
        .first()
    )
    if existing:
        structured_result = existing
        structured_result.content = json.dumps({})
        structured_result.status = OcrStatus.PROCESSING
        structured_result.created_at = get_beijing_time()
        db.commit()
        db.refresh(structured_result)
    else:
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


from app.services.analysis_components.entity_resolver import (
    EntityResolver,
    split_multi_person,
    _normalize_location,
    _extract_surname,
)

# ─────────────────────────────────────────────────────────────
#  跨文档分析主函数（v3 增强版）
# ─────────────────────────────────────────────────────────────

_EMPTY_VALS = {"未识别", "未知", "None", "none", "", "未记载"}


def _is_empty(val: Any) -> bool:
    return not val or str(val).strip() in _EMPTY_VALS


def _parse_price_to_float(price_str: str) -> Optional[float]:
    """
    尝试将古代价格文本解析为浮点数（单位：两）。
    支持："纹银八两五钱" → 8.5, "铜钱三千二百文" → 3200(文), "银十二两" → 12.0
    """
    if not price_str or _is_empty(price_str):
        return None

    _DIGIT_MAP = {
        "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "貳": 2,
        "三": 3, "叁": 3, "參": 3, "四": 4, "肆": 4, "五": 5, "伍": 5,
        "六": 6, "陆": 6, "陸": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
        "九": 9, "玖": 9, "十": 10, "拾": 10, "百": 100, "佰": 100,
        "千": 1000, "仟": 1000, "万": 10000, "萬": 10000,
    }

    arabic_match = re.search(r'(\d+(?:\.\d+)?)', price_str)
    if arabic_match:
        return float(arabic_match.group(1))

    total = 0.0
    liang_match = re.search(r'[银銀]?\s*([零〇一壹二贰貳三叁參四肆五伍六陆陸七柒八捌九玖十拾百佰千仟万萬]+)\s*两', price_str)
    qian_match = re.search(r'([零〇一壹二贰貳三叁參四肆五伍六陆陸七柒八捌九玖十拾]+)\s*钱', price_str)

    def _cn_to_int(s: str) -> int:
        result = 0
        current = 0
        for ch in s:
            v = _DIGIT_MAP.get(ch, -1)
            if v == -1:
                continue
            if v >= 10:
                if current == 0:
                    current = 1
                result += current * v
                current = 0
            else:
                current = v
        result += current
        return result

    if liang_match:
        total += _cn_to_int(liang_match.group(1))
    if qian_match:
        total += _cn_to_int(qian_match.group(1)) * 0.1

    return total if total > 0 else None


async def analyze_multi_task(multi_task_id: int, db: Session) -> None:
    """
    对 MultiTask 进行跨文档分析（v3 增强版）

    核心能力：
    1. 多人字段自动拆分（"张三、李四" → 两个独立实体）
    2. 增强实体消歧（异体字归一化 + 编辑距离 + 姓氏信号）
    3. 社会网络分析（介数中心性、聚集系数、社区检测）
    4. 宗族/家族检测（同姓聚类）
    5. 经济分析（价格趋势、交易规模）
    6. 见证人网络（中人活跃度与偏好）
    7. 时间序列地产流转链（带有向时间箭头）
    8. LLM 深度历史洞察
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

        parsed_datas: List[Dict] = []
        for sr in structured_results:
            try:
                parsed_datas.append(json.loads(sr.content))
            except json.JSONDecodeError:
                parsed_datas.append({})

        def _build_merged_graph():
            from collections import Counter, defaultdict

            G = nx.DiGraph()

            # ── 1. 收集原始节点（支持多人拆分）──────────────────
            raw_nodes = []
            for sr, data in zip(structured_results, parsed_datas):
                doc_id = str(sr.id)
                time_ad = data.get("Time_AD")
                location = str(data.get("Location", "")).strip()
                if location in _EMPTY_VALS:
                    location = ""
                for role in ["Seller", "Buyer", "Middleman"]:
                    name_str = str(data.get(role, "")).strip()
                    if not name_str or name_str in _EMPTY_VALS:
                        continue
                    persons = split_multi_person(name_str)
                    for person_name in persons:
                        raw_nodes.append({
                            "original_name": person_name,
                            "role": role,
                            "doc_id": doc_id,
                            "time_ad": time_ad,
                            "location": location,
                            "data": data,
                        })

            # ── 2. 实体消歧 ──────────────────────────────────────
            merged_entities = EntityResolver.resolve_entities(raw_nodes)

            name_to_entity: Dict[str, Dict] = {}
            for entity in merged_entities:
                for inst in entity["instances"]:
                    name_to_entity[inst["original_name"]] = entity

            # ── 3. 丰富统计信息 ──────────────────────────────────
            location_counter: Counter = Counter()
            int_years: List[int] = []
            prices: List[Dict] = []
            for sr_item, data in zip(structured_results, parsed_datas):
                loc = str(data.get("Location", "")).strip()
                if loc and loc not in _EMPTY_VALS:
                    location_counter[_normalize_location(loc)] += 1
                try:
                    y = int(data.get("Time_AD", ""))
                    int_years.append(y)
                except (ValueError, TypeError):
                    pass

                price_val = _parse_price_to_float(str(data.get("Price", "")))
                if price_val is not None:
                    year_val = None
                    try:
                        year_val = int(data.get("Time_AD", ""))
                    except (ValueError, TypeError):
                        pass
                    prices.append({
                        "doc_id": str(sr_item.id),
                        "year": year_val,
                        "price": price_val,
                        "raw": str(data.get("Price", "")),
                        "location": _normalize_location(loc) if loc and loc not in _EMPTY_VALS else "",
                    })

            time_range: Dict[str, Any] = {}
            if int_years:
                time_range = {
                    "start": min(int_years),
                    "end": max(int_years),
                    "span": max(int_years) - min(int_years),
                    "docs_with_time": len(int_years),
                }

            # 按出现文书数排序 top 人物
            top_people = sorted(
                merged_entities,
                key=lambda e: len(set(i["doc_id"] for i in e["instances"])),
                reverse=True,
            )[:8]

            # 地产流转（归一化地点后统计）
            norm_loc_counter: Counter = Counter()
            for data in parsed_datas:
                loc = str(data.get("Location", "")).strip()
                if loc and loc not in _EMPTY_VALS:
                    norm_loc_counter[_normalize_location(loc)] += 1

            land_locations = {loc for loc, cnt in norm_loc_counter.items() if cnt >= 2}
            land_chains = []
            for loc in land_locations:
                docs_here = []
                for sr_item, d in zip(structured_results, parsed_datas):
                    d_loc = _normalize_location(str(d.get("Location", "")).strip())
                    if d_loc == loc:
                        docs_here.append({
                            "doc_id": str(sr_item.id),
                            "time_ad": d.get("Time_AD"),
                            "time": d.get("Time", ""),
                            "seller": d.get("Seller", ""),
                            "buyer": d.get("Buyer", ""),
                            "price": d.get("Price", ""),
                        })
                # 按时间排序，构建流转序列
                def _sort_key(dd):
                    try:
                        return int(dd["time_ad"])
                    except (ValueError, TypeError):
                        return 99999
                docs_here.sort(key=_sort_key)

                try:
                    years = [int(dd["time_ad"]) for dd in docs_here
                             if dd.get("time_ad") and str(dd["time_ad"]) not in _EMPTY_VALS]
                except Exception:
                    years = []

                transfers = []
                for dd in docs_here:
                    t_info = {}
                    if dd.get("seller") and dd["seller"] not in _EMPTY_VALS:
                        t_info["from"] = split_multi_person(dd["seller"])[0] if split_multi_person(dd["seller"]) else dd["seller"]
                    if dd.get("buyer") and dd["buyer"] not in _EMPTY_VALS:
                        t_info["to"] = split_multi_person(dd["buyer"])[0] if split_multi_person(dd["buyer"]) else dd["buyer"]
                    if dd.get("time"):
                        t_info["time"] = dd["time"]
                    if dd.get("price") and dd["price"] not in _EMPTY_VALS:
                        t_info["price"] = dd["price"]
                    if t_info:
                        transfers.append(t_info)

                land_chains.append({
                    "location": loc,
                    "transaction_count": norm_loc_counter[loc],
                    "years": years,
                    "transfers": transfers[:10],
                })
            land_chains.sort(key=lambda c: c["transaction_count"], reverse=True)

            # ── 宗族/家族检测 ────────────────────────────────────
            surname_groups: Dict[str, List[str]] = defaultdict(list)
            for entity in merged_entities:
                sn = _extract_surname(entity["standard_name"])
                if sn:
                    surname_groups[sn].append(entity["standard_name"])
            clan_groups = [
                {"surname": sn, "members": members, "count": len(members)}
                for sn, members in surname_groups.items()
                if len(members) >= 2
            ]
            clan_groups.sort(key=lambda c: c["count"], reverse=True)

            # ── 见证人网络分析 ────────────────────────────────────
            witness_stats = []
            for entity in merged_entities:
                witness_instances = [i for i in entity["instances"] if i["role"] == "Middleman"]
                if len(witness_instances) >= 1:
                    witnessed_parties = set()
                    for inst in witness_instances:
                        doc_data = inst.get("data", {})
                        for r in ["Seller", "Buyer"]:
                            pname = str(doc_data.get(r, "")).strip()
                            if pname and pname not in _EMPTY_VALS:
                                for p in split_multi_person(pname):
                                    ent = name_to_entity.get(p)
                                    if ent:
                                        witnessed_parties.add(ent["standard_name"])
                    witness_stats.append({
                        "name": entity["standard_name"],
                        "witness_count": len(witness_instances),
                        "doc_count": len(set(i["doc_id"] for i in witness_instances)),
                        "witnessed_parties": list(witnessed_parties)[:10],
                    })
            witness_stats.sort(key=lambda w: w["witness_count"], reverse=True)

            # ── 时间分布统计 ─────────────────────────────────────
            year_distribution: Dict[int, int] = Counter()
            for y in int_years:
                decade = (y // 10) * 10
                year_distribution[decade] += 1
            decade_dist = [
                {"decade": f"{d}s", "year": d, "count": c}
                for d, c in sorted(year_distribution.items())
            ]

            # ── 价格趋势 ────────────────────────────────────────
            price_trend = sorted(
                [p for p in prices if p.get("year")],
                key=lambda p: p["year"]
            )

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
                    for loc, cnt in norm_loc_counter.most_common(8)
                ],
                "land_chain_count": len(land_chains),
                "land_chains": land_chains[:6],
                "clan_groups": clan_groups[:5],
                "witness_network": witness_stats[:6],
                "decade_distribution": decade_dist,
                "price_trend": price_trend[:20],
                "avg_price": (
                    round(sum(p["price"] for p in prices) / len(prices), 2)
                    if prices else None
                ),
                "total_transaction_value": (
                    round(sum(p["price"] for p in prices), 2)
                    if prices else None
                ),
            }

            # ── 4. 构建 NetworkX 有向图 ─────────────────────────
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
                           cross_role=is_cross,
                           surname=_extract_surname(name))

            for loc in land_locations:
                land_id = f"[地块]{loc}"
                G.add_node(land_id, category=3, doc_count=norm_loc_counter[loc],
                           roles_zh="地块", cross_role=False, surname="")

            # ── 5. 添加有向边 ───────────────────────────────────

            doc_entity_map: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
            for entity in merged_entities:
                for inst in entity["instances"]:
                    doc_entity_map[inst["doc_id"]][inst["role"]].append(entity["standard_name"])

            for sr_item, data in zip(structured_results, parsed_datas):
                doc_id = str(sr_item.id)
                de = doc_entity_map.get(doc_id, {})
                sellers = de.get("Seller", [])
                buyers = de.get("Buyer", [])
                middlemen = de.get("Middleman", [])
                time_label = str(data.get("Time_AD", "")).strip()
                if time_label in _EMPTY_VALS:
                    time_label = data.get("Time", "").strip()
                if time_label in _EMPTY_VALS:
                    time_label = ""

                price_raw = str(data.get("Price", "")).strip()
                price_label = f" [{price_raw}]" if price_raw and price_raw not in _EMPTY_VALS else ""

                # 买卖关系（有向：卖方 → 买方）
                for seller in sellers:
                    for buyer in buyers:
                        if seller != buyer and G.has_node(seller) and G.has_node(buyer):
                            edge_label = f"出售{'·' + time_label if time_label else ''}{price_label}"
                            if G.has_edge(seller, buyer):
                                G[seller][buyer]["doc_ids"] = G[seller][buyer].get("doc_ids", []) + [doc_id]
                                G[seller][buyer]["count"] = G[seller][buyer].get("count", 1) + 1
                            else:
                                G.add_edge(seller, buyer, relation="Trade",
                                           label=edge_label, doc_ids=[doc_id], count=1)

                # 见证关系（有向：中人 → 交易方）
                for middleman in middlemen:
                    for party in sellers + buyers:
                        if middleman != party and G.has_node(middleman) and G.has_node(party):
                            if G.has_edge(middleman, party):
                                G[middleman][party]["count"] = G[middleman][party].get("count", 1) + 1
                            else:
                                G.add_edge(middleman, party, relation="Witness",
                                           label="见证", doc_ids=[doc_id], count=1)

                # 地产流转
                loc = _normalize_location(str(data.get("Location", "")).strip())
                if loc and loc not in _EMPTY_VALS and loc in land_locations:
                    land_id = f"[地块]{loc}"
                    for seller in sellers:
                        if G.has_node(seller) and G.has_node(land_id):
                            edge_label = f"出让{'·' + time_label if time_label else ''}"
                            if not G.has_edge(seller, land_id):
                                G.add_edge(seller, land_id, relation="LandChange",
                                           label=edge_label, doc_ids=[doc_id], count=1)
                    for buyer in buyers:
                        if G.has_node(buyer) and G.has_node(land_id):
                            edge_label = f"受让{'·' + time_label if time_label else ''}"
                            if not G.has_edge(land_id, buyer):
                                G.add_edge(land_id, buyer, relation="LandChange",
                                           label=edge_label, doc_ids=[doc_id], count=1)

            # ── 6. 社会网络分析指标 ─────────────────────────────
            G_undirected = G.to_undirected()

            try:
                degree_centrality = nx.degree_centrality(G_undirected)
            except Exception:
                degree_centrality = {}

            try:
                betweenness = nx.betweenness_centrality(G_undirected)
            except Exception:
                betweenness = {}

            try:
                clustering = nx.clustering(G_undirected)
            except Exception:
                clustering = {}

            # 社区检测（基于 Louvain 或贪心模块度）
            communities = []
            try:
                from networkx.algorithms.community import greedy_modularity_communities
                comm_list = list(greedy_modularity_communities(G_undirected))
                for idx_c, comm in enumerate(comm_list):
                    if len(comm) >= 2:
                        communities.append({
                            "id": idx_c,
                            "members": [n for n in comm if not n.startswith("[地块]")],
                            "size": len([n for n in comm if not n.startswith("[地块]")]),
                        })
                communities.sort(key=lambda c: c["size"], reverse=True)
            except Exception:
                pass

            # 关键桥接人物（betweenness top 3）
            bridge_people = sorted(
                [(n, v) for n, v in betweenness.items() if not n.startswith("[地块]")],
                key=lambda x: x[1], reverse=True
            )[:3]

            statistics["network_metrics"] = {
                "avg_degree": round(sum(dict(G_undirected.degree()).values()) / max(G_undirected.number_of_nodes(), 1), 2),
                "density": round(nx.density(G_undirected), 4),
                "components": nx.number_connected_components(G_undirected),
                "bridge_people": [
                    {"name": n, "betweenness": round(v, 4)}
                    for n, v in bridge_people
                ],
                "communities": communities[:5],
            }

            # ── 7. 转换为 ECharts 格式 ─────────────────────────
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
                centrality_val = degree_centrality.get(node, 0)
                between_val = betweenness.get(node, 0)
                cluster_val = clustering.get(node, 0)
                is_land = cat_idx == 3

                base_size = 38 if is_land else 28
                size = min(85, base_size + centrality_val * 60 + doc_cnt * 6 + between_val * 30)

                color = NODE_COLORS.get(cat_idx, "#6b7280")
                border = NODE_BORDER.get(cat_idx, "#d1d5db")
                symbol = "roundRect" if is_land else ("diamond" if cat_idx == 4 else "circle")

                display_name = node.replace("[地块]", "") if is_land else node
                roles_zh = attrs.get("roles_zh", "")

                if is_land:
                    tooltip_text = f"地块：{display_name}<br/>交易次数：{doc_cnt}"
                else:
                    tooltip_parts = [
                        f"{display_name}",
                        f"角色：{roles_zh}",
                        f"涉及文书：{doc_cnt} 份",
                    ]
                    if between_val > 0.05:
                        tooltip_parts.append(f"桥接指数：{between_val:.2f}")
                    if cluster_val > 0:
                        tooltip_parts.append(f"聚集系数：{cluster_val:.2f}")
                    tooltip_text = "<br/>".join(tooltip_parts)

                echarts_nodes.append({
                    "name": node,
                    "category": cat_idx,
                    "symbolSize": size,
                    "symbol": symbol,
                    "value": tooltip_text,
                    "properties": {
                        "类型": "地块" if is_land else "人物",
                        "角色": roles_zh if not is_land else None,
                        "涉及文书数": f"{doc_cnt} 份" if not is_land else None,
                        "交易次数": f"{doc_cnt} 次" if is_land else None,
                        "桥接指数": f"{between_val:.3f}" if between_val > 0 and not is_land else None,
                    },
                    "label": {
                        "show": True,
                        "formatter": display_name,
                        "position": "bottom",
                        "fontSize": 12,
                        "fontWeight": "bold" if doc_cnt >= 2 or between_val > 0.1 else "normal",
                        "color": color,
                    },
                    "itemStyle": {
                        "color": color,
                        "borderColor": border,
                        "borderWidth": 3 if between_val > 0.1 else 2,
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
                
                # 提取精简标签（"出售", "见证", "出让", "受让" 等）
                short_lbl = lbl[:2] if lbl and len(lbl) >= 2 else lbl
                
                if count > 1:
                    lbl = f"{lbl}(×{count})"
                    short_lbl = f"{short_lbl}(×{count})"

                echarts_links.append({
                    "source": u,
                    "target": v,
                    "value": lbl,  # 悬停时显示完整信息（带时间价格）
                    "label": {
                        "show": True,  # 恢复显示
                        "formatter": short_lbl,  # 只显示精简的动作，如"出售"、"见证"
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
                        "curveness": 0.1 if rel == "Trade" else 0,
                    },
                })

            node_count = len(echarts_nodes)
            repulsion = max(600, min(1800, 400 + node_count * 50))
            edge_max = max(200, min(350, 150 + node_count * 8))

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
                        "edgeSymbol": ["none", "arrow"],
                        "edgeSymbolSize": [0, 8],
                        "label": {"position": "bottom", "formatter": "{b}"},
                        "labelLayout": {"hideOverlap": True},
                        "force": {
                            "repulsion": repulsion,
                            "edgeLength": [80, edge_max],
                            "gravity": 0.08,
                            "layoutAnimation": True,
                            "friction": 0.55,
                        },
                        "emphasis": {
                            "focus": "adjacency",
                            "lineStyle": {"width": 5},
                            "label": {"show": True, "fontWeight": "bold"},
                            "itemStyle": {"shadowBlur": 12, "shadowColor": "rgba(0,0,0,0.35)"},
                        },
                        "blur": {
                            "itemStyle": {"opacity": 0.15},
                            "lineStyle": {"opacity": 0.08},
                            "label": {"opacity": 0.2},
                        },
                    }
                ],
                "statistics": statistics,
            }

            return echarts_option, statistics, parsed_datas

        echarts_option, statistics, parsed_datas_out = await run_in_threadpool(_build_merged_graph)

        insights = await call_llm_for_insights(statistics, parsed_datas_out)
        echarts_option["insights"] = insights

        multi_relation_graph = MultiRelationGraph(
            multi_task_id=multi_task_id,
            content=json.dumps(echarts_option, ensure_ascii=False),
            status=OcrStatus.DONE,
            created_at=get_beijing_time(),
        )
        db.add(multi_relation_graph)
        db.commit()
        print(f"Multi-task analysis for {multi_task_id} completed (v3 enhanced).")

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

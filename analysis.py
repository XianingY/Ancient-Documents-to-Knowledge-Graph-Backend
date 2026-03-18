
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import networkx as nx
from sqlalchemy.orm import Session

from database import (
    StructuredResult, RelationGraph, MultiTask, MultiRelationGraph, 
    OcrResult, OcrStatus, MultiTaskStructuredResult, get_beijing_time
)

# 尝试导入 dashscope，如果不存在则使用模拟
try:
    import dashscope
    from dashscope import Generation
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
    if DASHSCOPE_API_KEY:
        dashscope.api_key = DASHSCOPE_API_KEY
    HAS_DASHSCOPE = True
except ImportError:
    HAS_DASHSCOPE = False

def call_llm_for_structure(text: str) -> Dict[str, Any]:
    """
    调用LLM进行结构化提取
    如果配置了DashScope且有API Key，则调用通义千问
    否则返回模拟数据
    """
    if HAS_DASHSCOPE and os.getenv("DASHSCOPE_API_KEY"):
        prompt = f"""
        你是一个专业的古籍文档分析专家。请分析以下地契文档的OCR识别结果，提取关键信息并以JSON格式返回。
        
        需要提取的字段如下：
        - Time: 契约签订时间（原文）
        - Time_AD: 契约签订时间（公元纪年，整数年份，如1832）
        - Location: 土地/房产位置
        - Seller: 卖方姓名
        - Buyer: 买方姓名
        - Middleman: 中人/见证人姓名
        - Price: 交易价格（包含单位）
        - Subject: 交易标的物（如田地面积、房屋等）
        - Translation: 文档的现代文翻译
        
        OCR文本内容：
        {text}
        
        请仅返回JSON字符串，不要包含markdown标记或其他无关内容。
        """
        
        try:
            response = Generation.call(
                model=Generation.Models.qwen_turbo,
                prompt=prompt,
                result_format='message'
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content
                # 清理markdown标记
                content = re.sub(r'^```json\s*', '', content)
                content = re.sub(r'\s*```$', '', content)
                return json.loads(content)
            else:
                print(f"LLM调用失败: {response.code} - {response.message}")
                # Fallback to mock
        except Exception as e:
            print(f"LLM调用异常: {e}")
            # Fallback to mock

    # 模拟数据提取逻辑 (简单的正则或规则，仅作演示)
    # 在实际没有LLM的情况下，这里应该尽可能用规则提取
    return {
        "Time": "未识别",
        "Time_AD": None,
        "Location": "未识别",
        "Seller": "未识别",
        "Buyer": "未识别",
        "Middleman": "未识别",
        "Price": "未识别",
        "Subject": "未识别",
        "Translation": "由于未配置有效的LLM API Key，无法生成翻译和精确提取。请配置DASHSCOPE_API_KEY环境变量。"
    }

def analyze_ocr_result(ocr_result_id: int, db: Session) -> None:
    """
    对OcrResult进行结构化分析
    
    Args:
        ocr_result_id: OCR结果的ID
        db: 数据库会话
    """
    try:
        # 获取OcrResult
        ocr_result = db.query(OcrResult).filter(OcrResult.id == ocr_result_id).first()
        if not ocr_result:
            print(f"OcrResult {ocr_result_id} not found")
            return
        
        if not ocr_result.raw_text:
            print(f"OcrResult {ocr_result_id} has no text")
            ocr_result.status = OcrStatus.FAILED
            db.commit()
            return

        # 更新状态
        # ocr_result.status = OcrStatus.PROCESSING # 已经是PROCESSING或DONE，这里不改OCR状态，而是创建新的记录
        
        # 调用分析逻辑
        structured_data = call_llm_for_structure(ocr_result.raw_text)
        
        # 补充文件名信息
        if ocr_result.image:
             structured_data["filename"] = ocr_result.image.filename

        # 创建StructuredResult
        structured_result = StructuredResult(
            ocr_result_id=ocr_result_id,
            content=json.dumps(structured_data, ensure_ascii=False),
            status=OcrStatus.DONE,
            created_at=get_beijing_time()
        )
        
        db.add(structured_result)
        db.commit()
        print(f"Structured analysis for {ocr_result_id} completed.")
        
    except Exception as e:
        print(f"Error analyzing OCR result {ocr_result_id}: {str(e)}")
        # 可以选择创建一个状态为FAILED的StructuredResult
        structured_result = StructuredResult(
            ocr_result_id=ocr_result_id,
            content=json.dumps({"error": str(e)}),
            status=OcrStatus.FAILED,
            created_at=get_beijing_time()
        )
        db.add(structured_result)
        db.commit()


def build_graph_from_structure(data: Dict[str, Any], doc_id: str) -> Dict[str, Any]:
    """
    基于结构化数据构建单文档关系图（与 app/services/analysis_service.py 保持同步）。
    节点不设 id 字段，连线直接用节点 name，避免 ECharts 按 id 匹配导致连线断开。
    """
    nodes: list = []
    links: list = []
    categories = [
        {"name": "卖方"}, {"name": "买方"}, {"name": "中人"}, {"name": "契约"}, {"name": "信息"},
    ]

    def is_empty(val) -> bool:
        return not val or str(val).strip() in ["未识别", "未知", "None", ""]

    def truncate(val: str, max_len: int = 10) -> str:
        s = str(val).strip()
        return s[:max_len] + "…" if len(s) > max_len else s

    CONTRACT = "地契"
    nodes.append({
        "name": CONTRACT, "category": 3, "symbolSize": 62, "symbol": "diamond",
        "value": "地契",
        "itemStyle": {"color": "#d97706", "borderColor": "#fbbf24", "borderWidth": 2},
        "label": {"show": True, "position": "inside", "fontSize": 16, "fontWeight": "bold", "color": "#fff"},
    })

    ROLE_COLORS = {0: "#dc2626", 1: "#2563eb", 2: "#059669"}

    def add_person(field_key: str, category_idx: int, rel_label: str, to_contract: bool):
        val = data.get(field_key)
        if is_empty(val):
            return
        name = str(val).strip()
        if any(n["name"] == name for n in nodes):
            return
        nodes.append({
            "name": name, "category": category_idx, "symbolSize": 48, "symbol": "circle",
            "value": name,
            "itemStyle": {"color": ROLE_COLORS[category_idx], "borderColor": "#fff", "borderWidth": 2},
            "label": {"show": True, "position": "bottom", "fontSize": 13, "fontWeight": "bold", "color": ROLE_COLORS[category_idx]},
        })
        src, tgt = (name, CONTRACT) if to_contract else (CONTRACT, name)
        links.append({
            "source": src, "target": tgt, "value": rel_label,
            "label": {"show": True, "formatter": rel_label, "fontSize": 11, "fontWeight": "bold"},
            "lineStyle": {"width": 2.5, "color": ROLE_COLORS[category_idx]},
        })

    add_person("Seller", 0, "卖出", True)
    add_person("Buyer", 1, "买入", False)
    add_person("Middleman", 2, "见证", True)

    for field_key, field_label in [("Time","时间"),("Time_AD","公元"),("Location","地点"),("Price","价格"),("Subject","标的")]:
        val = data.get(field_key)
        if is_empty(val):
            continue
        val_str = str(val).strip()
        display_name = f"公元{val_str}年" if field_key == "Time_AD" else f"{field_label}：{truncate(val_str)}"
        nodes.append({
            "name": display_name, "category": 4, "symbolSize": 34, "symbol": "roundRect",
            "value": val_str,
            "itemStyle": {"color": "#7c3aed", "borderColor": "#c4b5fd", "borderWidth": 1},
            "label": {"show": True, "position": "bottom", "fontSize": 11, "color": "#5b21b6"},
            "tooltip": {"formatter": f"<b>{field_label}</b><br/>{val_str}"},
        })
        links.append({
            "source": CONTRACT, "target": display_name,
            "lineStyle": {"type": "dashed", "width": 1.5, "color": "#a78bfa", "opacity": 0.7},
        })

    return {
        "type": "graph", "layout": "force", "categories": categories,
        "data": nodes, "links": links, "roam": True,
        "label": {"position": "bottom", "formatter": "{b}"},
        "lineStyle": {"curveness": 0.1},
    }

def analyze_structured_result(structured_result_id: int, db: Session) -> None:
    """
    对StructuredResult进行关系图分析
    """
    try:
        # 获取StructuredResult
        structured_result = db.query(StructuredResult).filter(StructuredResult.id == structured_result_id).first()
        if not structured_result:
            return
        
        try:
            data = json.loads(structured_result.content)
        except json.JSONDecodeError:
            print(f"Invalid JSON content in StructuredResult {structured_result_id}")
            return
            
        # 构建关系图
        graph_data = build_graph_from_structure(data, str(structured_result_id))
        
        # 封装成ECharts格式
        echarts_option = {
            "title": {"text": "地契关系图"},
            "tooltip": {"trigger": "item", "formatter": "{b}"},
            "legend": [{"data": ["卖方", "买方", "中人", "契约", "信息"]}],
            "series": [graph_data]
        }

        # 创建RelationGraph
        relation_graph = RelationGraph(
            structured_result_id=structured_result_id,
            content=json.dumps(echarts_option, ensure_ascii=False),
            status=OcrStatus.DONE,
            created_at=get_beijing_time()
        )
        
        db.add(relation_graph)
        db.commit()
        print(f"Relation graph analysis for {structured_result_id} completed.")
        
    except Exception as e:
        print(f"Error analyzing structured result {structured_result_id}: {str(e)}")
        relation_graph = RelationGraph(
            structured_result_id=structured_result_id,
            content=json.dumps({"error": str(e)}),
            status=OcrStatus.FAILED,
            created_at=get_beijing_time()
        )
        db.add(relation_graph)
        db.commit()


def calculate_similarity(node1_attrs, node2_attrs):
    """
    计算两个节点的相似度，用于实体消歧
    
    评分规则 (总分 1.0):
    1. 姓名完全匹配: +0.4
    2. 角色相同: +0.2
    3. 出现时间相近 (比如相差5年内): +0.2
    4. 地点相关 (相同或包含关系): +0.2
    
    阈值: > 0.7 判定为同一实体
    """
    score = 0.0
    
    # 1. 姓名匹配 (这是基础)
    name1 = node1_attrs.get("name", "")
    name2 = node2_attrs.get("name", "")
    if not name1 or not name2:
        return 0.0
    
    if name1 == name2:
        score += 0.4
    elif name1 in name2 or name2 in name1: # 部分匹配，如 "张三" 和 "张三丰"
        score += 0.2
    else:
        # 姓名完全不相关，直接返回0，不予合并
        return 0.0
        
    # 2. 角色匹配
    role1 = node1_attrs.get("role", "")
    role2 = node2_attrs.get("role", "")
    if role1 == role2:
        score += 0.2
    
    # 3. 时间匹配
    time1 = node1_attrs.get("time_ad")
    time2 = node2_attrs.get("time_ad")
    
    if time1 and time2:
        try:
            diff = abs(int(time1) - int(time2))
            if diff <= 5: # 5年内
                score += 0.2
            elif diff <= 20: # 20年内，可能是同一人不同时期
                score += 0.1
        except:
            pass # 解析失败忽略
    
    # 4. 地点匹配
    loc1 = node1_attrs.get("location", "")
    loc2 = node2_attrs.get("location", "")
    if loc1 and loc2:
        if loc1 == loc2:
            score += 0.2
        elif loc1 in loc2 or loc2 in loc1:
            score += 0.15
            
    return score

def analyze_multi_task(multi_task_id: int, db: Session) -> None:
    """
    对MultiTask进行跨文档分析
    """
    try:
        # 获取MultiTask及其关联的StructuredResult
        multi_task = db.query(MultiTask).filter(MultiTask.id == multi_task_id).first()
        if not multi_task:
            return
            
        associations = db.query(MultiTaskStructuredResult).filter(MultiTaskStructuredResult.multi_task_id == multi_task_id).all()
        structured_results = []
        for assoc in associations:
            sr = db.query(StructuredResult).filter(StructuredResult.id == assoc.structured_result_id).first()
            if sr:
                structured_results.append(sr)
        
        if not structured_results:
            print(f"No structured results found for MultiTask {multi_task_id}")
            return

        # 使用NetworkX构建合并图
        G = nx.Graph()
        
        # 1. 初始节点收集 (带属性)
        raw_nodes = []
        
        for sr in structured_results:
            try:
                data = json.loads(sr.content)
                doc_id = str(sr.id)
                time_ad = data.get("Time_AD")
                location = data.get("Location")
                
                # 提取各角色
                roles = ["Seller", "Buyer", "Middleman"]
                for role in roles:
                    name = data.get(role)
                    if name and name not in ["未识别", "未知", ""]:
                        raw_nodes.append({
                            "original_name": name,
                            "role": role,
                            "doc_id": doc_id,
                            "time_ad": time_ad,
                            "location": location,
                            "data": data # 保留原始数据引用
                        })
            except json.JSONDecodeError:
                continue
                
        # 2. 实体消歧与合并
        # 这是一个简单的聚类过程
        # merged_entities = { "实体ID": { "name": "...", "aliases": [], "docs": [...] } }
        merged_entities = []
        
        for node in raw_nodes:
            matched = False
            best_score = 0
            best_match_idx = -1
            
            # 尝试与已存在的实体合并
            for idx, entity in enumerate(merged_entities):
                # 取实体中已有的第一个记录作为代表进行比较
                representative = entity["instances"][0]
                
                # 构造比较所需的属性字典
                node_attrs = {
                    "name": node["original_name"],
                    "role": node["role"],
                    "time_ad": node["time_ad"],
                    "location": node["location"]
                }
                
                rep_attrs = {
                    "name": representative["original_name"],
                    "role": representative["role"],
                    "time_ad": representative["time_ad"],
                    "location": representative["location"]
                }
                
                score = calculate_similarity(node_attrs, rep_attrs)
                
                if score > 0.7 and score > best_score:
                    best_score = score
                    best_match_idx = idx
                    matched = True
            
            if matched:
                merged_entities[best_match_idx]["instances"].append(node)
                # 更新标准名 (可选: 选择出现次数最多的名字)
            else:
                # 创建新实体
                merged_entities.append({
                    "id": f"entity_{len(merged_entities)}",
                    "standard_name": node["original_name"],
                    "role": node["role"], # 初始角色
                    "instances": [node]
                })

        # 3. 构建图谱
        node_attributes = {}
        
        # 添加节点
        for entity in merged_entities:
            node_id = entity["standard_name"] # 使用标准名作为ID，或者使用 entity["id"]
            
            # 收集该实体涉及的所有文档ID
            related_docs = list(set([inst["doc_id"] for inst in entity["instances"]]))
            
            G.add_node(node_id, category=entity["role"])
            node_attributes[node_id] = {
                "role": entity["role"],
                "docs": related_docs,
                "instances_count": len(entity["instances"])
            }
            
        # 添加边 (基于文档共现关系)
        # 如果两个实体在同一个文档中出现，并且有交易关系，则连线
        for sr in structured_results:
            try:
                data = json.loads(sr.content)
                doc_id = str(sr.id)
                
                # 找到该文档中涉及的实体
                doc_entities = {} # role -> entity_name
                
                for role in ["Seller", "Buyer", "Middleman"]:
                    name = data.get(role)
                    if not name: continue
                    
                    # 查找该名字归属哪个合并后的实体
                    for entity in merged_entities:
                        for inst in entity["instances"]:
                            if inst["doc_id"] == doc_id and inst["original_name"] == name:
                                doc_entities[role] = entity["standard_name"]
                                break
                        if role in doc_entities: break
                
                seller = doc_entities.get("Seller")
                buyer = doc_entities.get("Buyer")
                middleman = doc_entities.get("Middleman")
                
                if seller and buyer:
                    G.add_edge(seller, buyer, relation="Trade", doc_id=doc_id)
                if middleman and seller:
                    G.add_edge(middleman, seller, relation="Witness", doc_id=doc_id)
                if middleman and buyer:
                    G.add_edge(middleman, buyer, relation="Witness", doc_id=doc_id)
                    
            except Exception:
                continue

        # 4. 转换为ECharts格式
        nodes = []
        categories = [{"name": "Seller"}, {"name": "Buyer"}, {"name": "Middleman"}]
        category_map = {"Seller": 0, "Buyer": 1, "Middleman": 2}
        
        # 计算中心性指标
        try:
            degree_centrality = nx.degree_centrality(G)
        except:
            degree_centrality = {}
            
        for node in G.nodes():
            cat_name = G.nodes[node].get("category", "Seller")
            cat_idx = category_map.get(cat_name, 0)
            
            # 根据度中心性调整节点大小
            size = 20 + (degree_centrality.get(node, 0) * 50)
            
            nodes.append({
                "id": node,
                "name": node,
                "category": cat_idx,
                "symbolSize": size,
                "value": degree_centrality.get(node, 0),
                "attributes": node_attributes.get(node, {})
            })
            
        links = []
        for u, v, data in G.edges(data=True):
            links.append({
                "source": u,
                "target": v,
                "value": data.get("relation", "link"),
                "label": {"show": True, "formatter": data.get("relation", "")}
            })
            
        echarts_option = {
            "title": {"text": "跨文档社会关系网络 (实体消歧增强版)"},
            "tooltip": {},
            "legend": [{"data": ["Seller", "Buyer", "Middleman"]}],
            "series": [{
                "type": "graph",
                "layout": "force",
                "categories": categories,
                "data": nodes,
                "links": links,
                "roam": True,
                "label": {"position": "right", "formatter": "{b}"},
                "force": {
                    "repulsion": 100
                }
            }]
        }
        
        # 创建MultiRelationGraph
        multi_relation_graph = MultiRelationGraph(
            multi_task_id=multi_task_id,
            content=json.dumps(echarts_option, ensure_ascii=False),
            status=OcrStatus.DONE,
            created_at=get_beijing_time()
        )
        
        db.add(multi_relation_graph)
        db.commit()
        print(f"Multi-task analysis for {multi_task_id} completed.")
        
    except Exception as e:
        print(f"Error analyzing multi task {multi_task_id}: {str(e)}")
        multi_relation_graph = MultiRelationGraph(
            multi_task_id=multi_task_id,
            content=json.dumps({"error": str(e)}),
            status=OcrStatus.FAILED,
            created_at=get_beijing_time()
        )
        db.add(multi_relation_graph)
        db.commit()

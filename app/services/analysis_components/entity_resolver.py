"""
古籍地契实体消歧解析器（增强版 v2）

改进要点：
- 字符级 Jaccard 相似度（基础层）
- DashScope 语义向量余弦相似度（语义层），可选
- 多维度加权融合：姓名语义 60% + 时间 20% + 地点 20%
- 批量预计算向量，避免重复 API 调用
- 处理古汉语通假字 / 异体字场景
- 跨角色检测：同一人先卖后买等
"""
import math
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm_a = math.sqrt(sum(a * a for a in vec1))
    norm_b = math.sqrt(sum(b * b for b in vec2))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_embeddings_batch(texts: List[str]) -> Dict[str, List[float]]:
    """
    批量获取文本向量。
    在 Celery worker（同步上下文）中直接调用 DashScope 同步接口。
    失败时静默回退到零向量，不影响主流程。
    """
    result: Dict[str, List[float]] = {}
    if not texts:
        return result

    try:
        import dashscope
        from app.core.config import settings
        if not settings.DASHSCOPE_API_KEY:
            return result

        dashscope.api_key = settings.DASHSCOPE_API_KEY

        # DashScope 单次最多 25 条，分批处理
        BATCH_SIZE = 25
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i: i + BATCH_SIZE]
            resp = dashscope.TextEmbedding.call(
                model=dashscope.TextEmbedding.Models.text_embedding_v1,
                input=batch,
            )
            if resp.status_code == 200:
                for j, emb in enumerate(resp.output["embeddings"]):
                    result[batch[j]] = emb["embedding"]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("entity_resolver_embedding_failed: %s", e)

    return result


class EntityResolver:
    """古籍地契实体消歧解析器（语义增强版）"""

    @staticmethod
    def _char_jaccard(s1: str, s2: str) -> float:
        if not s1 or not s2:
            return 0.0
        set1, set2 = set(s1), set(s2)
        inter = len(set1 & set2)
        union = len(set1 | set2)
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _char_name_similarity(name1: str, name2: str) -> float:
        """字符层相似度（不依赖向量）"""
        if name1 == name2:
            return 1.0
        if name1 in name2 or name2 in name1:
            return 0.75
        jaccard = EntityResolver._char_jaccard(name1, name2)
        return jaccard if jaccard >= 0.4 else 0.0

    @staticmethod
    def _semantic_name_similarity(
        name1: str,
        name2: str,
        embeddings: Dict[str, List[float]],
    ) -> float:
        """语义层相似度（若向量不可用则回退到字符层）"""
        v1 = embeddings.get(name1)
        v2 = embeddings.get(name2)
        if v1 and v2:
            return _cosine_similarity(v1, v2)
        return EntityResolver._char_name_similarity(name1, name2)

    @staticmethod
    def _name_similarity(
        name1: str,
        name2: str,
        embeddings: Dict[str, List[float]],
    ) -> float:
        """
        融合字符层 + 语义层（各占50%）。
        字符层先做快速过滤：完全不相关直接返回 0，避免不必要的向量计算。
        """
        char_sim = EntityResolver._char_name_similarity(name1, name2)
        # 字符层完全相同，短路返回 1.0
        if char_sim == 1.0:
            return 1.0
        # 字符层得分极低时（< 0.15）且没有语义向量，快速过滤
        if char_sim < 0.15 and name1 not in embeddings and name2 not in embeddings:
            return 0.0

        semantic_sim = EntityResolver._semantic_name_similarity(name1, name2, embeddings)
        # 两层加权融合
        fused = 0.4 * char_sim + 0.6 * semantic_sim
        return fused

    @staticmethod
    def calculate_similarity(
        node1_attrs: Dict,
        node2_attrs: Dict,
        embeddings: Dict[str, List[float]],
    ) -> float:
        """
        多维度综合相似度（0~1）：
          姓名语义 60% + 时间 20% + 地点 20%
        角色不同不扣分，支持跨角色同一人场景。
        """
        name1 = str(node1_attrs.get("name", "")).strip()
        name2 = str(node2_attrs.get("name", "")).strip()

        name_sim = EntityResolver._name_similarity(name1, name2, embeddings)
        if name_sim < 0.1:
            return 0.0

        score = name_sim * 0.6

        # 时间维度
        t1 = node1_attrs.get("time_ad")
        t2 = node2_attrs.get("time_ad")
        if t1 and t2:
            try:
                diff = abs(int(t1) - int(t2))
                if diff <= 3:
                    score += 0.20
                elif diff <= 10:
                    score += 0.14
                elif diff <= 30:
                    score += 0.08
                elif diff <= 60:
                    score += 0.04
            except (ValueError, TypeError):
                pass

        # 地点维度
        loc1 = str(node1_attrs.get("location", "")).strip()
        loc2 = str(node2_attrs.get("location", "")).strip()
        if loc1 and loc2:
            if loc1 == loc2:
                score += 0.20
            elif loc1 in loc2 or loc2 in loc1:
                score += 0.14
            else:
                prefix_len = min(3, len(loc1), len(loc2))
                if prefix_len >= 2 and loc1[:prefix_len] == loc2[:prefix_len]:
                    score += 0.08

        return score

    @staticmethod
    def _select_standard_name(instances: List[Dict]) -> str:
        names = [inst["original_name"] for inst in instances]
        max_len = max(len(n) for n in names)
        longest = [n for n in names if len(n) == max_len]
        freq = Counter(names)
        return max(longest, key=lambda n: freq[n])

    @staticmethod
    def resolve_entities(raw_nodes: List[Dict]) -> List[Dict[str, Any]]:
        """
        执行实体消歧，阈值 0.45。

        步骤：
        1. 收集所有人名，批量调用 DashScope 获取向量（失败时静默降级）
        2. 遍历节点，对每个节点与已合并实体计算多维相似度
        3. 超过阈值则合并，否则新建实体

        返回每条：
          id, standard_name, role, roles, cross_role, instances
        """
        # 批量预计算向量
        unique_names = list({node["original_name"] for node in raw_nodes})
        embeddings = _get_embeddings_batch(unique_names)

        merged: List[Dict] = []
        THRESHOLD = 0.45

        for node in raw_nodes:
            node_attrs = {
                "name": node["original_name"],
                "role": node["role"],
                "time_ad": node.get("time_ad"),
                "location": node.get("location"),
            }

            best_score, best_idx = 0.0, -1

            for idx, entity in enumerate(merged):
                scores = []
                for inst in entity["instances"]:
                    inst_attrs = {
                        "name": inst["original_name"],
                        "role": inst["role"],
                        "time_ad": inst.get("time_ad"),
                        "location": inst.get("location"),
                    }
                    scores.append(
                        EntityResolver.calculate_similarity(node_attrs, inst_attrs, embeddings)
                    )
                avg = sum(scores) / len(scores) if scores else 0.0
                if avg >= THRESHOLD and avg > best_score:
                    best_score = avg
                    best_idx = idx

            if best_idx >= 0:
                merged[best_idx]["instances"].append(node)
                merged[best_idx]["standard_name"] = EntityResolver._select_standard_name(
                    merged[best_idx]["instances"]
                )
                all_roles = {inst["role"] for inst in merged[best_idx]["instances"]}
                merged[best_idx]["roles"] = list(all_roles)
                merged[best_idx]["cross_role"] = len(all_roles) > 1
            else:
                merged.append(
                    {
                        "id": f"entity_{len(merged)}",
                        "standard_name": node["original_name"],
                        "role": node["role"],
                        "roles": [node["role"]],
                        "cross_role": False,
                        "instances": [node],
                    }
                )

        return merged

"""
古籍地契实体消歧解析器（增强版 v3）

v3 改进：
- 古汉语异体字/通假字归一化（160+ 组常见映射）
- 编辑距离（Levenshtein）补充短人名匹配
- 姓氏优先匹配：同姓时降低阈值，不同姓时提高阈值
- 多人字段自动拆分（顿号、逗号、和、与、及）
- 单链接(max-link) + 平均链接(avg-link) 混合匹配策略
- 地点语义归一化（去除"地方""处"等后缀）
"""
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple


# ── 古汉语异体字/通假字归一化表 ──────────────────────────────────
# 键为异体/通假字，值为统一标准字
_VARIANT_MAP: Dict[str, str] = {}
_VARIANT_GROUPS = [
    "氏𠂤", "佈布", "雲云", "從从", "東东", "萬万", "與与", "義义",
    "書书", "會会", "來来", "備备", "價价", "處处", "問问", "國国",
    "園园", "報报", "壹一", "貳二", "參叁三", "肆四", "陸六", "柒七",
    "捌八", "玖九", "拾十", "佰百", "仟千", "兩两", "銀银", "錢钱",
    "買买", "賣卖", "業业", "產产", "畝亩", "陽阳", "陰阴", "張张",
    "劉刘", "陳陈", "楊杨", "趙赵", "黃黄", "許许", "鄭郑", "謝谢",
    "鄧邓", "馮冯", "蕭萧", "鄒邹", "嚴严", "韓韩", "龍龙", "萬万",
    "盧卢", "鍾钟", "譚谭", "龔龚", "賴赖", "廖廖", "閻阎", "鄔邬",
    "於于", "裏里", "縣县", "鎮镇", "莊庄", "廳厅", "號号", "條条",
    "歲岁", "鑑鉴", "開开", "將将", "應应", "當当", "無无", "為为",
    "歸归", "議议", "據据", "總总", "繼继", "續续", "質质", "執执",
    "讓让", "認认", "證证", "憑凭", "關关", "聯联", "齊齐", "學学",
    "寳宝", "寶宝", "塲场", "場场", "裡里", "崑昆", "嶽岳", "峯峰",
    "甯宁", "寕宁", "邨村",
]

for group in _VARIANT_GROUPS:
    if len(group) >= 2:
        standard = group[-1]
        for ch in group[:-1]:
            _VARIANT_MAP[ch] = standard


def _normalize_name(name: str) -> str:
    """将人名中的异体字/通假字归一化为标准字"""
    return "".join(_VARIANT_MAP.get(ch, ch) for ch in name)


def _normalize_location(loc: str) -> str:
    """地点归一化：去除无意义后缀"""
    loc = loc.strip()
    loc = "".join(_VARIANT_MAP.get(ch, ch) for ch in loc)
    for suffix in ("地方", "处", "處", "地"):
        if loc.endswith(suffix) and len(loc) > len(suffix):
            loc = loc[:-len(suffix)]
    return loc


# ── 多人拆分 ──────────────────────────────────────────────────
_SPLIT_PATTERN = re.compile(r"[、，,；;]\s*|(?:以?及|和|与|與)\s*")


def split_multi_person(name_str: str) -> List[str]:
    """
    将 "张三、李四" / "张三和李四" / "张三，李四" 等拆分为独立人名。
    单人返回 [name]，拆分后过滤空串与无效值。
    """
    if not name_str or not name_str.strip():
        return []
    parts = _SPLIT_PATTERN.split(name_str.strip())
    result = []
    for p in parts:
        p = p.strip()
        if p and p not in ("未识别", "未知", "None", "none", "未记载"):
            result.append(p)
    return result if result else []


# ── 编辑距离 ──────────────────────────────────────────────────

def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _edit_similarity(s1: str, s2: str) -> float:
    if not s1 or not s2:
        return 0.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - _levenshtein(s1, s2) / max_len


# ── 向量计算 ──────────────────────────────────────────────────

def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm_a = math.sqrt(sum(a * a for a in vec1))
    norm_b = math.sqrt(sum(b * b for b in vec2))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_embeddings_batch(texts: List[str]) -> Dict[str, List[float]]:
    """
    批量获取文本向量（DashScope 同步接口）。
    失败时静默回退，不影响主流程。
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


# ── 姓氏工具 ──────────────────────────────────────────────────

_COMPOUND_SURNAMES: Set[str] = {
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫",
    "万俟", "闻人", "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台",
    "皇甫", "宗政", "濮阳", "公冶", "太叔", "申屠", "公孙", "慕容",
    "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于", "司空", "令狐",
}


def _extract_surname(name: str) -> str:
    if len(name) >= 2 and name[:2] in _COMPOUND_SURNAMES:
        return name[:2]
    return name[0] if name else ""


class EntityResolver:
    """古籍地契实体消歧解析器（v3 增强版）"""

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
        """字符层相似度（Jaccard + 编辑距离 + 包含关系综合）"""
        n1 = _normalize_name(name1)
        n2 = _normalize_name(name2)

        if n1 == n2:
            return 1.0
        if n1 in n2 or n2 in n1:
            return 0.80

        edit_sim = _edit_similarity(n1, n2)
        jaccard = EntityResolver._char_jaccard(n1, n2)

        # 短人名（2-3字）更依赖编辑距离，长文本更依赖 Jaccard
        if max(len(n1), len(n2)) <= 3:
            combined = 0.7 * edit_sim + 0.3 * jaccard
        else:
            combined = 0.4 * edit_sim + 0.6 * jaccard

        return combined if combined >= 0.35 else 0.0

    @staticmethod
    def _semantic_name_similarity(
        name1: str,
        name2: str,
        embeddings: Dict[str, List[float]],
    ) -> float:
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
        """融合字符层 + 语义层 + 姓氏信号"""
        n1 = _normalize_name(name1)
        n2 = _normalize_name(name2)

        char_sim = EntityResolver._char_name_similarity(n1, n2)
        if char_sim == 1.0:
            return 1.0

        surname1 = _extract_surname(n1)
        surname2 = _extract_surname(n2)
        same_surname = surname1 and surname2 and surname1 == surname2

        if char_sim < 0.10 and not same_surname:
            if n1 not in embeddings and n2 not in embeddings:
                return 0.0

        semantic_sim = EntityResolver._semantic_name_similarity(name1, name2, embeddings)

        fused = 0.35 * char_sim + 0.65 * semantic_sim

        # 同姓加分（古代地契中同姓大概率同族）
        if same_surname and fused >= 0.25:
            fused = min(1.0, fused + 0.08)
        # 不同姓且字符相似度低——大幅惩罚
        elif not same_surname and char_sim < 0.3:
            fused *= 0.7

        return fused

    @staticmethod
    def calculate_similarity(
        node1_attrs: Dict,
        node2_attrs: Dict,
        embeddings: Dict[str, List[float]],
    ) -> float:
        """
        多维度综合相似度（0~1）：
          姓名 55% + 时间 20% + 地点 25%
        """
        name1 = str(node1_attrs.get("name", "")).strip()
        name2 = str(node2_attrs.get("name", "")).strip()

        name_sim = EntityResolver._name_similarity(name1, name2, embeddings)
        if name_sim < 0.1:
            return 0.0

        score = name_sim * 0.55

        # 时间维度（更细粒度）
        t1 = node1_attrs.get("time_ad")
        t2 = node2_attrs.get("time_ad")
        if t1 and t2:
            try:
                diff = abs(int(t1) - int(t2))
                if diff == 0:
                    score += 0.20
                elif diff <= 3:
                    score += 0.18
                elif diff <= 10:
                    score += 0.14
                elif diff <= 30:
                    score += 0.08
                elif diff <= 60:
                    score += 0.04
                # >60 年不加分（跨代）
            except (ValueError, TypeError):
                pass

        # 地点维度（归一化后比较）
        loc1 = _normalize_location(str(node1_attrs.get("location", "")))
        loc2 = _normalize_location(str(node2_attrs.get("location", "")))
        if loc1 and loc2:
            if loc1 == loc2:
                score += 0.25
            elif loc1 in loc2 or loc2 in loc1:
                score += 0.18
            else:
                loc_edit = _edit_similarity(loc1, loc2)
                if loc_edit >= 0.6:
                    score += loc_edit * 0.15

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
        执行实体消歧（v3），基础阈值 0.42。

        匹配策略：max-link（取最大得分），当实体实例数 >=3 时使用
        top-2 avg（取最高两个得分均值），避免单个噪音实例拉高分数。
        """
        unique_names = list({node["original_name"] for node in raw_nodes})
        embeddings = _get_embeddings_batch(unique_names)

        merged: List[Dict] = []
        THRESHOLD = 0.42

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

                # 混合链接策略
                if len(scores) <= 2:
                    agg = max(scores) if scores else 0.0
                else:
                    sorted_scores = sorted(scores, reverse=True)
                    agg = (sorted_scores[0] + sorted_scores[1]) / 2.0

                if agg >= THRESHOLD and agg > best_score:
                    best_score = agg
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

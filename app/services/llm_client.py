"""
LLM 调用工具模块
封装 DashScope Qwen-Turbo 的结构化提取和历史洞察生成，供其他 service 模块调用。
"""
import json
import re
from typing import Any, Dict, List

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings

try:
    import dashscope
    from dashscope import Generation
    if settings.DASHSCOPE_API_KEY:
        dashscope.api_key = settings.DASHSCOPE_API_KEY
    HAS_DASHSCOPE = True
except ImportError:
    HAS_DASHSCOPE = False


# ── 结构化提取 ───────────────────────────────────────────────

_STRUCTURE_FALLBACK: Dict[str, Any] = {
    "Time": "未识别",
    "Time_AD": None,
    "Location": "未识别",
    "Seller": "未识别",
    "Buyer": "未识别",
    "Middleman": "未识别",
    "Price": "未识别",
    "Subject": "未识别",
    "Translation": "未配置 DASHSCOPE_API_KEY，无法生成译文。",
}

# ── Pass 1：信息提取专用 Prompt ───────────────────────────────
_EXTRACT_SYSTEM = """\
你是一名专研明清地契文书的历史文献专家。
请从古代契约 OCR 文本中精确提取以下字段，以合法 JSON 返回，字段含义如下：

- Time        : 契约签订的原文纪年（如"道光十二年三月"）
- Time_AD     : 对应公元年份整数（无法判断时填 null）
- Location    : 土地或房产的具体位置描述
- Seller      : 卖方/出租方姓名（多人时用顿号分隔）
- Buyer       : 买方/承租方姓名（多人时用顿号分隔）
- Middleman   : 中人/见证人/代书人姓名（多人时用顿号分隔）
- Price       : 交易价格，含货币单位（如"纹银八两五钱"）
- Subject     : 交易标的物描述（如"旱地一亩三分"、"瓦房三间"）

规则：
1. 只输出纯 JSON，不加 markdown 代码块标记
2. 原文中没有的字段填 "未记载"
3. 不要推断或补充原文没有的内容
"""

# ── Pass 2：译文专用 Prompt ───────────────────────────────────
_TRANSLATE_SYSTEM = """\
你是一名精通古代汉语的历史文献研究员，专门从事明清地契文书的白话文翻译。

翻译要求：
1. 将原文的文言文逐句翻译为现代标准汉语（白话文）
2. 保留人名、地名、官职名等专有名词，不做更改
3. 大写数字（壹贰叁肆…）翻译为阿拉伯数字或汉字小写数字
4. 计量单位（亩、分、厘、两、钱、文）保持不变，在括号内注明换算（如有把握）
5. 契约套语（如"恐口无凭，立此契约为据"）翻译为通顺的现代表述
6. 因图片损毁而出现的 □ 占位符在译文中保留为"（字迹不清）"
7. 译文应忠实原文，不添加原文没有的解释或评论
8. 输出格式：直接输出译文，不加任何前缀或说明
"""


def _parse_json_response(content: str) -> Dict[str, Any]:
    """从 LLM 响应中提取 JSON，兼容多种格式（代码块、混合文本等）"""
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return json.loads(cleaned)


def _call_llm_messages(system: str, user: str, model: str = "qwen-plus",
                       temperature: float = 0.1, top_p: float = 0.3) -> str:
    """通用 messages 格式调用，返回纯文本。默认低温度以获取确定性输出。"""
    response = Generation.call(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        result_format="message",
        temperature=temperature,
        top_p=top_p,
    )
    if response.status_code == 200:
        return response.output.choices[0].message.content.strip()
    raise RuntimeError(f"LLM 调用失败: {response.code} - {response.message}")


def call_structure_llm_sync(text: str) -> Dict[str, Any]:
    """
    两阶段结构化分析：
      Pass 1（qwen-plus）：提取结构化字段（Time / Seller / Buyer 等）
      Pass 2（qwen-plus）：生成高质量白话文译文
    两个任务分开调用，互不干扰，均使用更强模型。
    """
    if not (HAS_DASHSCOPE and settings.DASHSCOPE_API_KEY):
        return _STRUCTURE_FALLBACK.copy()

    result = _STRUCTURE_FALLBACK.copy()

    # ── Pass 1：结构化字段提取 ────────────────────────────────
    try:
        raw = _call_llm_messages(
            system=_EXTRACT_SYSTEM,
            user=f"以下是古代契约文书的 OCR 文本，请提取结构化信息：\n\n{text}",
            model="qwen-plus",
        )
        extracted = _parse_json_response(raw)
        # 只接受已知字段，防止意外键污染
        for key in ("Time", "Time_AD", "Location", "Seller", "Buyer",
                    "Middleman", "Price", "Subject"):
            if key in extracted:
                result[key] = extracted[key]
    except Exception as e:
        print(f"[llm_client] 结构化提取失败: {e}")

    # ── Pass 2：白话文译文 ────────────────────────────────────
    try:
        translation = _call_llm_messages(
            system=_TRANSLATE_SYSTEM,
            user=f"请将以下古代契约文书翻译为现代白话文：\n\n{text}",
            model="qwen-plus",
        )
        result["Translation"] = translation
    except Exception as e:
        print(f"[llm_client] 译文生成失败: {e}")
        result["Translation"] = "译文生成失败，请重新分析。"

    return result


async def call_structure_llm(text: str) -> Dict[str, Any]:
    """call_structure_llm_sync 的异步包装"""
    return await run_in_threadpool(call_structure_llm_sync, text)


# ── 历史洞察生成 ─────────────────────────────────────────────

def _build_insights_prompt(statistics: Dict[str, Any], parsed_datas: List[Dict]) -> str:
    """根据统计数据构造 LLM 多维分析提示词"""
    doc_count = statistics.get("doc_count", 0)
    time_range = statistics.get("time_range", {})
    unique_people = statistics.get("unique_people", 0)
    cross_role = statistics.get("cross_role_people", [])
    top_people = statistics.get("top_people", [])
    top_locations = statistics.get("top_locations", [])
    land_chain_count = statistics.get("land_chain_count", 0)
    land_chains = statistics.get("land_chains", [])
    clan_groups = statistics.get("clan_groups", [])
    witness_network = statistics.get("witness_network", [])
    network_metrics = statistics.get("network_metrics", {})
    price_trend = statistics.get("price_trend", [])
    avg_price = statistics.get("avg_price")
    decade_distribution = statistics.get("decade_distribution", [])

    time_str = (
        f"公元 {time_range['start']} 年 — {time_range['end']} 年（跨度 {time_range.get('span', 0)} 年）"
        if time_range.get("start") and time_range.get("end")
        else "时间信息不完整"
    )

    _EMPTY = {"未识别", "未知", "", "未记载", "None", "none"}
    summaries = []
    for d in parsed_datas[:12]:
        seller = d.get("Seller", "")
        buyer = d.get("Buyer", "")
        if seller and buyer and all(v not in _EMPTY for v in [seller, buyer]):
            loc = d.get("Location", "")
            price = d.get("Price", "")
            subject = d.get("Subject", "")
            t = d.get("Time", "")
            parts = [f"{t}：" if t and t not in _EMPTY else ""]
            parts.append(f"{seller} → {buyer}")
            if loc and loc not in _EMPTY:
                parts.append(f"，地点：{loc}")
            if subject and subject not in _EMPTY:
                parts.append(f"，标的：{subject}")
            if price and price not in _EMPTY:
                parts.append(f"，价格：{price}")
            summaries.append("  - " + "".join(parts))

    cross_str = "、".join(cross_role[:5]) if cross_role else "无"
    top_people_str = (
        "、".join([f"{p['name']}（{p['doc_count']}份·{'、'.join(p.get('roles', []))}）" for p in top_people[:5]])
        if top_people else "数据不足"
    )
    locations_str = "、".join([f"{l['name']}({l['count']}次)" for l in top_locations[:5]]) if top_locations else "未提取到"
    tx_block = "\n".join(summaries) if summaries else "  （未能提取有效交易摘要）"

    # 家族/宗族信息
    clan_str = ""
    if clan_groups:
        clan_parts = [f"{c['surname']}姓{c['count']}人（{'、'.join(c['members'][:4])}）" for c in clan_groups[:3]]
        clan_str = "、".join(clan_parts)
    else:
        clan_str = "未发现明显宗族聚集"

    # 见证人网络
    witness_str = ""
    if witness_network:
        w_parts = [f"{w['name']}（见证{w['witness_count']}次）" for w in witness_network[:3]]
        witness_str = "、".join(w_parts)
    else:
        witness_str = "数据不足"

    # 地产流转链详情
    chain_details = []
    for chain in land_chains[:3]:
        transfers = chain.get("transfers", [])
        if transfers:
            t_strs = [f"{t.get('from', '?')}→{t.get('to', '?')}" + (f"({t['time']})" if t.get('time') else "") for t in transfers[:4]]
            chain_details.append(f"  · {chain['location']}：{'，'.join(t_strs)}")
    chain_block = "\n".join(chain_details) if chain_details else "  （无详细流转记录）"

    # 社会网络指标
    net_info = ""
    if network_metrics:
        bridge_str = "、".join([f"{b['name']}" for b in network_metrics.get("bridge_people", [])]) or "无"
        net_info = f"网络密度 {network_metrics.get('density', 0):.3f}，桥接关键人物：{bridge_str}"
    else:
        net_info = "尚未计算"

    # 价格信息
    price_info = ""
    if price_trend:
        price_info = f"可识别价格的交易 {len(price_trend)} 笔"
        if avg_price:
            price_info += f"，均价约 {avg_price} 两"
        if len(price_trend) >= 2:
            earliest = price_trend[0]
            latest = price_trend[-1]
            if earliest.get("year") and latest.get("year"):
                price_info += f"（{earliest['year']}年 {earliest['raw']} → {latest['year']}年 {latest['raw']}）"
    else:
        price_info = "价格数据不足"

    # 时间分布
    decade_str = ""
    if decade_distribution:
        decade_str = "、".join([f"{d['decade']}({d['count']}份)" for d in decade_distribution])
    else:
        decade_str = "时间数据不足"

    return f"""你是专业的历史文书研究专家，精通中国明清社会经济史、土地制度史和地方社会网络研究。
请根据以下跨文档知识图谱分析结果，撰写300-450字的深度分析报告。

【基础数据】
- 文书总量：{doc_count} 份地契
- 时间范围：{time_str}
- 涉及人物：{unique_people} 人
- 时代分布：{decade_str}

【交易明细】
{tx_block}

【社会网络分析】
- 核心人物：{top_people_str}
- 角色切换人物：{cross_str}
- 宗族聚集：{clan_str}
- 活跃见证人：{witness_str}
- 网络结构：{net_info}

【地产流转】
- 多次易手地块：{land_chain_count} 处
- 流转链详情：
{chain_block}
- 主要交易地点：{locations_str}

【经济数据】
- {price_info}

【分析要求】
请从以下维度中选择2-3个有数据支撑的角度展开深度分析：
1. **社会网络**：人际关系网络的结构特征，核心人物的社会资本，见证人的信用网络
2. **土地流转**：地权变动的历史脉络，同一地块多次易手的深层原因
3. **经济形态**：交易价格反映的土地经济状况，货币形态与市场化程度
4. **宗族社会**：同姓聚集与宗族土地经营，家族间的土地往来模式
5. **时空特征**：交易的时间分布规律，地理空间聚集与扩散

要求：语言专业凝练，论述有据，每个分析角度需结合具体人名/地名/时间等数据，不臆测无据内容。"""


def _generate_fallback_insights(statistics: Dict[str, Any]) -> str:
    """LLM 不可用时生成模板化洞察文字"""
    doc_count = statistics.get("doc_count", 0)
    time_range = statistics.get("time_range", {})
    unique_people = statistics.get("unique_people", 0)
    cross_role = statistics.get("cross_role_people", [])
    top_people = statistics.get("top_people", [])
    land_chain_count = statistics.get("land_chain_count", 0)
    clan_groups = statistics.get("clan_groups", [])
    witness_network = statistics.get("witness_network", [])
    network_metrics = statistics.get("network_metrics", {})
    avg_price = statistics.get("avg_price")

    parts = [f"本次跨文档分析共涉及 {doc_count} 份地契文书，"]
    if time_range.get("start") and time_range.get("end"):
        parts.append(
            f"时间跨度从公元 {time_range['start']} 年至 {time_range['end']} 年"
            f"（历时约 {time_range.get('span', 0)} 年），"
        )
    parts.append(f"共涉及 {unique_people} 位历史人物。")

    if cross_role:
        names = "、".join(cross_role[:3])
        parts.append(
            f"其中 {len(cross_role)} 人曾在不同文书中兼任多重角色（{names}），"
            "体现了地方社会中个人土地权益的动态变化。"
        )
    if top_people:
        top_names = "、".join([p["name"] for p in top_people[:3]])
        parts.append(f"文书网络中的核心人物包括 {top_names}，在多份地契中频繁出现。")
    if clan_groups:
        for cg in clan_groups[:2]:
            parts.append(f"{cg['surname']}姓家族有 {cg['count']} 人参与土地交易，体现了宗族经济的特征。")
    if witness_network:
        w = witness_network[0]
        parts.append(f"最活跃的见证人为{w['name']}，共参与 {w['witness_count']} 次见证，是地方信用网络的关键节点。")
    if land_chain_count > 0:
        parts.append(f"同一地块被多次转让的情况共出现 {land_chain_count} 处，反映了土地产权的频繁流动。")
    if network_metrics.get("bridge_people"):
        bridge = network_metrics["bridge_people"][0]
        parts.append(f"社会网络分析显示，{bridge['name']}是跨群体交往的关键桥接人物。")
    if avg_price:
        parts.append(f"可识别的交易平均价格约 {avg_price} 两。")

    return "".join(parts)


def call_insights_llm_sync(statistics: Dict[str, Any], parsed_datas: List[Dict]) -> str:
    """调用 LLM 生成跨文档历史洞察（同步）"""
    if not (HAS_DASHSCOPE and settings.DASHSCOPE_API_KEY):
        return _generate_fallback_insights(statistics)
    try:
        prompt = _build_insights_prompt(statistics, parsed_datas)
        response = Generation.call(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}],
            result_format="message",
        )
        if response.status_code == 200:
            content = response.output.choices[0].message.content.strip()
            return content if content else _generate_fallback_insights(statistics)
        print(f"LLM洞察生成失败: {response.code} - {response.message}")
    except Exception as e:
        print(f"LLM洞察生成异常: {e}")
    return _generate_fallback_insights(statistics)


async def call_insights_llm(statistics: Dict[str, Any], parsed_datas: List[Dict]) -> str:
    """call_insights_llm_sync 的异步包装"""
    return await run_in_threadpool(call_insights_llm_sync, statistics, parsed_datas)

"""
Dream 生成服务。

负责从原始会话事件中生成 Dream（梦境），提取：
- 核心洞见（Insights）
- 知识碎片（Sparks）
- 待探索问题（Follow-ups）
- 关键决策
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from ai_memory_hub.core.config import LlmConfig, MemoryConfig
from ai_memory_hub.core.dream_models import Dream, Spark, utc_now as dream_utc_now
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.utils import project_key_from_path, trim_excerpt
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.storage.dream_store import DreamStore

# 模块级 logger，由具体函数初始化
_module_logger = None


# 默认的会话结束标记
SESSION_END_MARKERS = (
    "bye", "goodbye", "thanks", "thank you",
    "再见", "谢谢", "感谢",
    "会话结束", "end of session", "session ended",
)

# 决策关键词
DECISION_KEYWORDS = (
    "决定", "采用", "选择", "我们用", "使用",
    "decide", "adopt", "choose", "use", "we will",
    "结论是", "因此", "最终", "选择方案",
)


def _detect_category(text: str) -> str:
    """根据会话内容检测类别。"""
    text_lower = text.lower()
    if any(k in text_lower for k in ["bug", "error", "修复", "调试", "fix"]):
        return "debug"
    if any(k in text_lower for k in ["创建", "实现", "写", "build", "create", "implement"]):
        return "creation"
    if any(k in text_lower for k in ["学习", "理解", "研究", "learn", "understand"]):
        return "learning"
    if any(k in text_lower for k in ["决策", "选择方案", "architecture", "设计"]):
        return "decision"
    return "exploration"


def _extract_decisions(messages: list[dict[str, Any]]) -> list[str]:
    """从消息中提取关键决策。"""
    decisions: list[str] = []
    for msg in messages:
        content = msg.get("text") or msg.get("content") or ""
        for keyword in DECISION_KEYWORDS:
            if keyword in content.lower():
                sentences = re.split(r"[。.!?\n]", content)
                for sentence in sentences:
                    if keyword in sentence.lower() and len(sentence.strip()) > 10:
                        decisions.append(trim_excerpt(sentence.strip(), 150))
                break
    seen: set[str] = set()
    unique: list[str] = []
    for d in decisions:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique[:5]


def _extract_insights(messages: list[dict[str, Any]]) -> list[str]:
    """从消息中提取核心洞见。"""
    insights: list[str] = []
    for msg in messages:
        content = msg.get("text") or msg.get("content") or ""
        content_lower = content.lower()
        if any(k in content_lower for k in ["发现", "关键", "重要的是", "需要记住", "insight", "important", "key point"]):
            sentences = re.split(r"[。.!?]", content)
            for sentence in sentences:
                s = sentence.strip()
                if len(s) > 15:
                    for marker in ["发现", "关键", "重要", "insight", "important", "key", "记住"]:
                        if marker in s:
                            insights.append(trim_excerpt(s, 200))
                            break
    seen: set[str] = set()
    unique: list[str] = []
    for insight in insights:
        normalized = insight.lower()
        if normalized not in seen and len(normalized) > 15:
            seen.add(normalized)
            unique.append(insight)
    return unique[:5]


def _extract_sparks(messages: list[dict[str, Any]]) -> list[Spark]:
    """从消息中提取知识碎片。"""
    sparks: list[Spark] = []
    code_pattern = re.compile(r"```(\w*)\n([\s\S]*?)```")
    for msg in messages:
        content = msg.get("text") or msg.get("content") or ""
        for match in code_pattern.finditer(content):
            lang = match.group(1) or "code"
            code = match.group(2).strip()
            sparks.append(Spark(
                content=f"代码片段 ({lang})",
                code_snippet=code[:500],
                language=lang,
                source_excerpt=trim_excerpt(content, 100),
                tags=[lang],
            ))
        if not sparks and len(content) > 50 and len(content) < 300:
            if any(k in content for k in ["pattern", "模式", "方法", "最佳实践", "best practice"]):
                sparks.append(Spark(
                    content=trim_excerpt(content, 200),
                    source_excerpt=trim_excerpt(content, 100),
                ))
    return sparks[:5]


def _extract_follow_ups(messages: list[dict[str, Any]]) -> list[str]:
    """从消息中提取待探索问题。"""
    follow_ups: list[str] = []
    question_pattern = re.compile(
        r"(可以进一步|下一步|未来|有待|还需要|后续探索|"
        r"future|next step|explore|investigate|later|to do|待研究|待探索)[:\s]*([^.!?\n]{10,100})",
        re.IGNORECASE,
    )
    for msg in messages:
        content = msg.get("text") or msg.get("content") or ""
        for match in question_pattern.finditer(content):
            follow_ups.append(trim_excerpt(match.group(0), 150))
    return list(dict.fromkeys(follow_ups))[:3]


def _generate_title(messages: list[dict[str, Any]]) -> str:
    """根据会话内容生成标题。"""
    first_user_msg = ""
    for msg in messages:
        role = msg.get("role", "").lower()
        content = msg.get("text") or msg.get("content") or ""
        if role in ("user", "human") and len(content) > 10:
            first_user_msg = content
            break
        if role in ("user", "human"):
            first_user_msg = content
    if not first_user_msg:
        for msg in messages:
            content = msg.get("text") or msg.get("content") or ""
            if content:
                first_user_msg = content
                break
    title = trim_excerpt(first_user_msg, 60)
    category = _detect_category(" ".join(msg.get("text", "") or msg.get("content", "") for msg in messages))
    if not title:
        title = f"关于 {category} 的讨论"
    return title


def _call_llm_for_dream(
    messages: list[dict[str, Any]],
    config: MemoryConfig,
    session_id: str,
    source_tool: str,
) -> dict[str, Any] | None:
    """调用 LLM API 生成 Dream。"""
    global _module_logger
    if _module_logger is None:
        _module_logger = get_logger(config.data_home_path / "logs")
    logger = _module_logger

    if not config.llm.is_complete():
        return None

    import httpx
    conversation_text = "\n".join(
        f"[{msg.get('role', 'unknown')}]: {msg.get('text') or msg.get('content', '')}"
        for msg in messages
    )

    prompt = f"""你是一个 AI 会话分析专家。请分析以下对话，生成一个「梦境」（Dream）摘要。

要求：
1. 提取 2-5 个核心洞见（insights）
2. 提取 0-5 个知识碎片（sparks），包括可复用的代码片段、模式
3. 提取 0-3 个待探索问题（follow-ups）
4. 提取 0-3 个关键决策（key_decisions）
5. 判断会话类别（category）：exploration/creation/debug/learning/decision
6. 生成一个简短摘要（summary，不超过 100 字）

会话内容：
{conversation_text[:4000]}

请以 JSON 格式输出：
{{
    "title": "梦境标题（不超过60字）",
    "category": "类别",
    "summary": "简短摘要（不超过100字）",
    "insights": ["洞见1", "洞见2", ...],
    "sparks": [
        {{"content": "碎片描述", "code_snippet": "代码（可选）", "language": "语言（可选）", "tags": ["标签"]}}
    ],
    "follow_ups": ["待探索问题1", ...],
    "key_decisions": ["决策1", ...]
}}

只输出 JSON，不要其他内容。"""

    import json as json_module

    max_retries = 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=float(config.llm.timeout_seconds)) as client:
                response = client.post(
                    f"{config.llm.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.llm.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.llm.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 1500,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    for line in content.split("\n"):
                        line = line.strip()
                        if line.startswith("{") and line.endswith("}"):
                            return json_module.loads(line)
                        if line.startswith("```json"):
                            continue
                        if line.startswith("```"):
                            continue
                    for start in range(len(content) - 1, -1, -1):
                        if content[start] == "}":
                            for end in range(start + 1, len(content)):
                                if content[end] == "{":
                                    try:
                                        return json_module.loads(content[start:] + content[end:])
                                    except Exception:
                                        pass
                            break
                    return json_module.loads(content)
                elif response.status_code == 429:
                    # Rate limited - wait longer
                    import time
                    time.sleep(2 ** attempt)
                    continue
        except httpx.HTTPError as e:
            last_error = e
            logger.warning(f"LLM call attempt {attempt + 1} failed (retry {max_retries - attempt - 1} left): {e}")
            import time
            time.sleep(2 ** attempt)  # Exponential backoff
            continue

    # All retries failed
    if last_error:
        logger.error(f"LLM call failed after {max_retries} retries: {last_error}")
    return None


def generate_dream_from_messages(
    messages: list[dict[str, Any]],
    source_session: str,
    source_tool: str,
    source_path: str,
    config: MemoryConfig | None = None,
) -> Dream:
    """从消息列表生成 Dream。"""
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()

    dream_store = DreamStore(config)
    llm_result = _call_llm_for_dream(messages, config, source_session, source_tool)

    if llm_result:
        sparks = []
        for spark_data in llm_result.get("sparks", []):
            if isinstance(spark_data, dict):
                sparks.append(Spark(
                    content=spark_data.get("content", ""),
                    code_snippet=spark_data.get("code_snippet"),
                    language=spark_data.get("language"),
                    tags=list(spark_data.get("tags", [])),
                ))

        dream = Dream(
            id=dream_store.new_dream_id(),
            source_session=source_session,
            source_tool=source_tool,
            source_path=source_path,
            generated_at=dream_utc_now(),
            title=llm_result.get("title", _generate_title(messages)),
            category=llm_result.get("category", _detect_category(" ".join(m.get("text", "") or "" for m in messages))),
            insights=list(llm_result.get("insights", [])),
            sparks=sparks,
            follow_ups=list(llm_result.get("follow_ups", [])),
            key_decisions=list(llm_result.get("key_decisions", [])),
            summary=llm_result.get("summary", ""),
            message_count=len(messages),
            participant_tools=[source_tool],
            related_project=project_key_from_path(source_path),
            status="active",
        )
    else:
        # 降级：使用规则方法提取
        insights = _extract_insights(messages)
        sparks = _extract_sparks(messages)
        follow_ups = _extract_follow_ups(messages)
        decisions = _extract_decisions(messages)

        dream = Dream(
            id=dream_store.new_dream_id(),
            source_session=source_session,
            source_tool=source_tool,
            source_path=source_path,
            generated_at=dream_utc_now(),
            title=_generate_title(messages),
            category=_detect_category(" ".join(m.get("text", "") or "" for m in messages)),
            insights=insights,
            sparks=sparks,
            follow_ups=follow_ups,
            key_decisions=decisions,
            summary=trim_excerpt(" ".join(insights[:2]), 100) if insights else "",
            message_count=len(messages),
            participant_tools=[source_tool],
            related_project=project_key_from_path(source_path),
            status="active",
        )

    return dream


def generate_dreams_from_raw_events(
    raw_events: list[dict[str, Any]],
    source_session: str,
    source_tool: str,
    source_path: str,
    config: MemoryConfig | None = None,
) -> list[Dream]:
    """从原始事件列表生成 Dreams。

    将连续的消息分组为会话段，每个段生成一个 Dream。
    """
    if not raw_events:
        return []

    # 按会话分组
    messages: list[dict[str, Any]] = []
    for event in raw_events:
        text = event.get("text") or event.get("content")
        if not text:
            continue
        role = event.get("role", "assistant")
        messages.append({"role": role, "text": text, "timestamp": event.get("timestamp")})

    if not messages:
        return []

    # 简单策略：如果消息数少于 4 个，整体作为一个 Dream
    if len(messages) < 4:
        dream = generate_dream_from_messages(messages, source_session, source_tool, source_path, config)
        return [dream]

    # 消息数较多时，按每 20 条消息分一段
    dreams: list[Dream] = []
    chunk_size = 20
    for i in range(0, len(messages), chunk_size):
        chunk = messages[i:i + chunk_size]
        sub_session = f"{source_session}-chunk-{i // chunk_size}"
        dream = generate_dream_from_messages(chunk, sub_session, source_tool, source_path, config)
        dreams.append(dream)

    return dreams


def run_dream_generate(config: MemoryConfig | None = None) -> dict[str, Any]:
    """生成所有待处理的 Dreams。

    扫描原始事件，生成新的 Dreams。
    """
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()

    logger = get_logger(config.data_home_path / "logs")
    store = MemoryStore(config)
    dream_store = DreamStore(config)
    store.ensure_layout()
    dream_store.ensure_layout()

    logger.info("Starting Dream generation...")

    # 获取原始事件
    raw_events = store.list_raw_events()
    if not raw_events:
        logger.info("No raw events found")
        return {"dreams_generated": 0, "status": "no_events"}

    # 按会话分组
    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in raw_events:
        session_id = row["session_id"] or "unknown"
        if session_id not in by_session:
            by_session[session_id] = []
        by_session[session_id].append(dict(row))

    # 检查已有 Dream 避免重复
    existing_index = dream_store.get_index()
    generated = 0

    for session_id, events in by_session.items():
        # 跳过已有该会话的 Dream
        for existing_id in existing_index:
            if existing_id.startswith(f"dream-") and session_id in existing_id:
                continue

        messages: list[dict[str, Any]] = []
        for event in events:
            text = event.get("text") or event.get("content")
            if text:
                messages.append({
                    "role": event.get("role", "assistant"),
                    "text": text,
                    "timestamp": event.get("timestamp"),
                })

        if not messages:
            continue

        source_path = events[0].get("source_path", "") if events else ""
        source_tool = events[0].get("source_tool", "unknown") if events else "unknown"

        dreams = generate_dreams_from_raw_events(
            messages,
            session_id,
            source_tool,
            source_path,
            config,
        )

        for dream in dreams:
            dream_store.write_dream(dream)
            generated += 1

    logger.info(f"Dream generation completed: {generated} dreams generated")

    return {
        "dreams_generated": generated,
        "total_dreams": dream_store.count_dreams(),
        "status": "completed",
    }


def run_dream_for_session(
    session_id: str,
    config: MemoryConfig | None = None,
) -> dict[str, Any]:
    """为指定会话生成 Dream。"""
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()

    store = MemoryStore(config)
    dream_store = DreamStore(config)

    raw_events = store.list_raw_events()
    session_events = [dict(row) for row in raw_events if row["session_id"] == session_id]

    if not session_events:
        return {"status": "not_found", "session_id": session_id}

    messages: list[dict[str, Any]] = []
    for event in session_events:
        text = event.get("text") or event.get("content")
        if text:
            messages.append({
                "role": event.get("role", "assistant"),
                "text": text,
                "timestamp": event.get("timestamp"),
            })

    if not messages:
        return {"status": "no_messages", "session_id": session_id}

    source_path = session_events[0].get("source_path", "") if session_events else ""
    source_tool = session_events[0].get("source_tool", "unknown") if session_events else "unknown"

    dreams = generate_dreams_from_raw_events(messages, session_id, source_tool, source_path, config)

    for dream in dreams:
        dream_store.write_dream(dream)

    return {
        "status": "success",
        "session_id": session_id,
        "dreams_generated": len(dreams),
        "dream_ids": [d.id for d in dreams],
    }

import httpx

from app.config import settings


class TavilyNotConfiguredError(RuntimeError):
    pass


async def search_web(query: str, *, max_results: int = 5) -> str:
    query = query.strip()
    if not query:
        return "你想查什么？可以直接说：帮我查一下飞书任务管理 API。"
    if not settings.tavily_api_key:
        raise TavilyNotConfiguredError("Tavily API Key 未配置，请在 .env 中添加 TAVILY_API_KEY=tvly-你的key")

    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
        "include_images": False,
        "include_favicon": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.tavily_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(settings.tavily_api_url, headers=headers, json=payload)

    if response.status_code == 401:
        raise RuntimeError("Tavily API Key 无效或未授权")
    if response.status_code == 429:
        raise RuntimeError("Tavily 调用次数过多或额度不足，请稍后再试")
    response.raise_for_status()

    data = response.json()
    answer = str(data.get("answer") or "").strip()
    results = data.get("results") or []

    lines: list[str] = []
    if answer:
        lines.append(f"搜索结论：\n{answer}")
    else:
        lines.append("搜索结果：")

    for index, item in enumerate(results[:max_results], start=1):
        title = str(item.get("title") or "未命名结果").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        if len(content) > 180:
            content = content[:180].rstrip() + "..."
        block = f"{index}. {title}"
        if url:
            block += f"\n{url}"
        if content:
            block += f"\n{content}"
        lines.append(block)

    return "\n\n".join(lines)[:3800]

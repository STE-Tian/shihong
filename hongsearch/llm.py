"""OpenAI 兼容的对话接口。没有密钥时不会发请求。"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class LLMError(RuntimeError):
    pass


def chat_completions_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("未配置 LLM_BASE_URL。")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


@dataclass
class LLMClient:
    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.strip())

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        if not self.enabled:
            raise LLMError("未配置 LLM_API_KEY。")
        url = chat_completions_url(self.base_url)
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": messages,
            "max_tokens": 1800,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMError(f"大模型接口连接失败：{exc.__class__.__name__}") from exc
        body_text = response.text.lower()
        if response.status_code >= 400 and "max_tokens" in body_text and (
            "unsupported" in body_text or "not supported" in body_text or "max_completion_tokens" in body_text
        ):
            payload.pop("max_tokens", None)
            payload["max_completion_tokens"] = 1800
            try:
                response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
            except httpx.HTTPError as exc:
                raise LLMError(f"大模型接口连接失败：{exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            detail = response.text[:300].replace(self.api_key, "[已隐藏]")
            raise LLMError(f"大模型接口返回 {response.status_code}：{detail}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("大模型返回的内容无法解析。") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("大模型没有返回正文。")
        return content.strip()

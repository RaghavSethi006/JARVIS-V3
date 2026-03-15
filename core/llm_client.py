"""
core/llm_client.py

Unified LLM client. Abstracts Groq API as primary,
local llama-cpp as fallback. All LLM calls in the codebase go through here.
"""

import asyncio
import os
from typing import AsyncIterator, Optional
from core.logger import logger
from config.manager import config  # Ensures .env is loaded


class LLMClient:
    """
    Singleton LLM client. Call LLMClient.get() to get the instance.

    Usage:
        client = LLMClient.get()
        response = await client.complete(messages=[...], system="...")
        async for chunk in client.stream(messages=[...], system="..."):
            print(chunk)
    """

    _instance: Optional["LLMClient"] = None

    def __init__(self) -> None:
        self._client = None
        self._local_llm = None
        self._mode: str = os.environ.get("LLM_MODE", "groq").lower()
        self._api_key: str = os.environ.get("LLM_API_KEY", "")
        self.MODEL: str = os.environ.get("LLM_API_MODEL", "llama-3.3-70b-versatile")
        self.MAX_TOKENS: int = int(os.environ.get("LLM_MAX_TOKENS", "1024"))
        self.TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
        self._init_client()

    @classmethod
    def get(cls) -> "LLMClient":
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Initialisation ────────────────────────────────────────────────────

    def _init_client(self) -> None:
        """Attempt to initialise the primary provider; fall back to local."""
        # Map old env values for backwards compat
        if self._mode in ("api", "groq"):
            self._mode = "groq"
            try:
                from groq import AsyncGroq  # type: ignore

                self._client = AsyncGroq(api_key=self._api_key)
                logger.info(f"LLMClient: Using Groq ({self.MODEL})")
            except ImportError:
                logger.error("LLMClient: groq package not installed. Falling back to local.")
                self._mode = "local"
            except Exception as e:
                logger.error(f"LLMClient: Groq init failed: {e}. Falling back to local.")
                self._mode = "local"

        if self._mode == "local":
            self._init_local()

    def _init_local(self) -> None:
        """Fallback: load llama-cpp model."""
        try:
            from llama_cpp import Llama  # type: ignore

            model_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models", "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
            )
            if os.path.exists(model_path):
                self._local_llm = Llama(
                    model_path=model_path,
                    n_ctx=8192,
                    n_threads=4,
                    verbose=False,
                )
                logger.info("LLMClient: Local llama-cpp model loaded.")
            else:
                logger.warning(f"LLMClient: No local model found at {model_path}.")
                self._local_llm = None
        except ImportError:
            logger.warning("LLMClient: llama-cpp-python not installed. Local fallback unavailable.")
            self._local_llm = None
        except Exception as e:
            logger.error(f"LLMClient: Local model init failed: {e}")
            self._local_llm = None

    # ── Public API ────────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Single completion. Returns full response as string.
        messages format: [{"role": "user"|"assistant", "content": "..."}]
        """
        _max = max_tokens or self.MAX_TOKENS
        _temp = temperature if temperature is not None else self.TEMPERATURE

        if self._mode == "groq":
            return await self._complete_groq(messages, system, _max, _temp)
        return await self._complete_local(messages, system, _max)

    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Streaming completion. Yields text chunks as they arrive."""
        if self._mode == "groq":
            async for chunk in self._stream_groq(
                messages, system, max_tokens or self.MAX_TOKENS
            ):
                yield chunk
        else:
            # Local model doesn't stream – yield full response at once
            result = await self._complete_local(
                messages, system, max_tokens or self.MAX_TOKENS
            )
            yield result

    # ── Groq provider ─────────────────────────────────────────────────────

    async def _complete_groq(
        self, messages: list[dict], system: str, max_tokens: int, temperature: float
    ) -> str:
        api_messages: list[dict] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self.MODEL,
                    messages=api_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return (response.choices[0].message.content or "").strip()
            except Exception as e:
                if attempt < 2:
                    wait = (attempt + 1) * 2
                    logger.warning(f"LLMClient: Groq attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"LLMClient: Groq API error after 3 attempts: {e}. Falling back to local.")
        
        # Attempt local fallback on API failure
        if self._local_llm is not None:
            logger.info("LLMClient: Falling back to local model for this request.")
            return await self._complete_local(messages, system, max_tokens)
        return "I encountered an issue with my language model. Please try again."

    async def _stream_groq(
        self, messages: list[dict], system: str, max_tokens: int
    ) -> AsyncIterator[str]:
        try:
            api_messages: list[dict] = []
            if system:
                api_messages.append({"role": "system", "content": system})
            api_messages.extend(messages)

            stream = await self._client.chat.completions.create(
                model=self.MODEL,
                messages=api_messages,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"LLMClient: Groq stream error: {e}")
            yield "I encountered an issue. Please try again."

    # ── Local fallback ────────────────────────────────────────────────────

    async def _complete_local(
        self, messages: list[dict], system: str, max_tokens: int
    ) -> str:
        if self._local_llm is None:
            return "Language model unavailable. Please check your API key or install a local model."

        loop = asyncio.get_event_loop()

        def _run() -> str:
            api_messages: list[dict] = []
            if system:
                api_messages.append({"role": "system", "content": system})
            api_messages.extend(messages)
            result = self._local_llm.create_chat_completion(
                messages=api_messages,
                max_tokens=max_tokens,
            )
            return (result["choices"][0]["message"]["content"] or "").strip()

        return await loop.run_in_executor(None, _run)

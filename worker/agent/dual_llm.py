import asyncio
import time
import json
from groq import AsyncGroq
from worker.config import config


class DualGroqRouter:
    """
    Dual Groq API key router for gpt-oss-120b.
    Max context: 4096 tokens.
    Round-robin + failover between two keys.
    On rate limit: switch key, if both limited wait 60s.
    On token exceed: summarize older messages, wait 60s.
    """

    def __init__(self):
        self.client_1 = AsyncGroq(api_key=config.GROQ_API_KEY) if config.GROQ_API_KEY else None
        self.client_2 = AsyncGroq(api_key=config.GROQ_API_KEY_2) if config.GROQ_API_KEY_2 else None
        self.model = config.GROQ_MODEL
        self.max_tokens = config.GROQ_MAX_TOKENS
        self.safe_tokens = config.GROQ_SAFE_TOKENS
        self.retry_wait = config.AGENT_RETRY_WAIT
        self.max_retries = config.AGENT_MAX_RETRIES
        self._current_key = 1
        self._key_1_limited_until = 0
        self._key_2_limited_until = 0

    def _count_tokens_approx(self, messages):
        """Approximate token count (~4 chars per token for English)"""
        total = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        total += len(part.get('text', '')) // 4
                    else:
                        total += len(str(part)) // 4
            else:
                total += len(str(content)) // 4
            if 'tool_calls' in msg:
                for tc in msg['tool_calls']:
                    total += len(json.dumps(tc.get('function', {}))) // 4
        return total

    def _get_client(self):
        """Get least rate-limited client"""
        now = time.time()
        key1_ok = self.client_1 and now > self._key_1_limited_until
        key2_ok = self.client_2 and now > self._key_2_limited_until

        if key1_ok and key2_ok:
            if self._current_key == 1:
                self._current_key = 2
                return self.client_1, 1
            else:
                self._current_key = 1
                return self.client_2, 2
        elif key1_ok:
            return self.client_1, 1
        elif key2_ok:
            return self.client_2, 2
        else:
            if self.client_1 and self.client_2:
                if self._key_1_limited_until < self._key_2_limited_until:
                    return self.client_1, 1
                return self.client_2, 2
            elif self.client_1:
                return self.client_1, 1
            elif self.client_2:
                return self.client_2, 2
            return None, 0

    async def _compress_messages(self, messages):
        """Compress messages to fit within 4096 token context.
        Keep system prompt + last 2 messages intact.
        Summarize everything else into one message."""

        if len(messages) <= 3:
            return messages

        system_msgs = [m for m in messages if m.get('role') == 'system']
        non_system = [m for m in messages if m.get('role') != 'system']

        recent = non_system[-4:] if len(non_system) > 4 else non_system
        older = non_system[:-4] if len(non_system) > 4 else []

        if not older:
            return messages

        summary_parts = []
        for m in older:
            role = m.get('role', 'unknown')
            content = m.get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    p.get('text', '') for p in content
                    if isinstance(p, dict) and 'text' in p
                )
            summary_parts.append(f"[{role}]: {str(content)[:200]}")

        summary_text = "\n".join(summary_parts)

        max_summary_chars = self.safe_tokens * 2
        if len(summary_text) > max_summary_chars:
            summary_text = summary_text[:max_summary_chars] + "\n...[truncated]"

        compressed = system_msgs + [{
            "role": "system",
            "content": f"[Previous conversation summary]\n{summary_text}"
        }] + recent

        return compressed

    async def complete(self, messages, tools=None, max_retries=None):
        """Main completion method with dual-key failover."""
        max_retries = max_retries or self.max_retries

        for attempt in range(max_retries):
            client, key_num = self._get_client()

            if not client:
                raise RuntimeError("No Groq API keys configured")

            now = time.time()
            limited_until = (
                self._key_1_limited_until if key_num == 1
                else self._key_2_limited_until
            )
            if now < limited_until:
                wait = int(limited_until - now) + 1
                print(f"[DualGroq] Key {key_num} rate limited, waiting {wait}s...")
                await asyncio.sleep(wait)
                continue

            token_count = self._count_tokens_approx(messages)

            if token_count > self.safe_tokens:
                print(f"[DualGroq] Token count ~{token_count} > {self.safe_tokens}, compressing...")
                messages = await self._compress_messages(messages)
                new_count = self._count_tokens_approx(messages)
                if new_count > self.safe_tokens:
                    print(f"[DualGroq] Still ~{new_count} tokens after compression, waiting {self.retry_wait}s...")
                    await asyncio.sleep(self.retry_wait)
                    continue

            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": 0.3,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                print(f"[DualGroq] Calling key {key_num}, ~{token_count} tokens, attempt {attempt+1}")
                response = await client.chat.completions.create(**kwargs)

                return response

            except Exception as e:
                error_str = str(e)

                if "429" in error_str or "rate_limit" in error_str.lower():
                    wait_time = self.retry_wait
                    if key_num == 1:
                        self._key_1_limited_until = time.time() + wait_time
                    else:
                        self._key_2_limited_until = time.time() + wait_time

                    print(f"[DualGroq] Key {key_num} rate limited, switching key...")

                    other_client = self.client_2 if key_num == 1 else self.client_1
                    other_key = 2 if key_num == 1 else 1
                    other_limited = (
                        self._key_2_limited_until if key_num == 1
                        else self._key_1_limited_until
                    )

                    if other_client and time.time() > other_limited:
                        try:
                            print(f"[DualGroq] Trying key {other_key} as fallback...")
                            response = await other_client.chat.completions.create(**kwargs)
                            return response
                        except Exception as e2:
                            if "429" in str(e2):
                                if other_key == 1:
                                    self._key_1_limited_until = time.time() + wait_time
                                else:
                                    self._key_2_limited_until = time.time() + wait_time

                    print(f"[DualGroq] Both keys rate limited, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue

                elif "context" in error_str.lower() or "token" in error_str.lower():
                    print(f"[DualGroq] Token limit exceeded, compressing and waiting {self.retry_wait}s...")
                    messages = await self._compress_messages(messages)
                    await asyncio.sleep(self.retry_wait)
                    continue

                else:
                    print(f"[DualGroq] Error: {error_str[:200]}, attempt {attempt+1}/{max_retries}")
                    await asyncio.sleep(5)
                    continue

        raise RuntimeError(f"All LLM retries exhausted after {max_retries} attempts")


# Singleton
llm_router = None


def get_llm_router():
    global llm_router
    if llm_router is None:
        llm_router = DualGroqRouter()
    return llm_router

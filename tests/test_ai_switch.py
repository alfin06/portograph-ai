"""Paid AI calls stay off unless AI_ENABLED is set: the agents refuse to build a client
and /reflect answers without calling anything. No network."""
import asyncio
from types import SimpleNamespace

import pytest

from core import config


def test_ai_is_off_by_default(monkeypatch):
    monkeypatch.delenv("AI_ENABLED", raising=False)
    assert config._env("AI_ENABLED", "false").lower() not in ("1", "true", "yes")


def test_agents_refuse_while_off(monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    from agents.critic_agent import CriticAgent
    with pytest.raises(config.AIDisabled):
        CriticAgent()
    from modules.crypto.screener_agent import ScreenerAgent
    with pytest.raises(config.AIDisabled):
        ScreenerAgent()


def test_reflect_replies_without_calling_the_model(monkeypatch):
    import core.telegram_bot as bot
    monkeypatch.setattr(config, "AI_ENABLED", False)
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(bot, "CriticAgent", lambda: pytest.fail("the AI agent must not be created"))
    replies = []

    async def reply_text(text, **kw):
        replies.append(text)

    who = SimpleNamespace(id=42)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=42, type="private"), effective_user=who,
                             message=SimpleNamespace(reply_text=reply_text, chat_id=42))
    asyncio.run(bot.reflect(update, SimpleNamespace(user_data={})))
    assert replies == [config.AI_OFF_MESSAGE]

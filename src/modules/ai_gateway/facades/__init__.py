"""Provider facades behind :class:`~src.modules.ai_gateway.gateway.AIGateway` (D-067).

Each facade translates one provider's failures into the twelve :class:`ErrorCategory`
values of ``contract.py`` and never leaks SDK exceptions or message text.
"""

from src.modules.ai_gateway.facades.fake import FakeProvider
from src.modules.ai_gateway.facades.openai_compatible import HttpOpenAICompatibleProvider

__all__ = ["FakeProvider", "HttpOpenAICompatibleProvider"]

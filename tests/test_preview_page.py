import httpx

from agentic_intent_gate.adapters.local_http import create_app
from agentic_intent_gate.application.generate_plan import PlanningService


async def test_same_origin_page_load_does_not_invoke_provider(validator, provider_factory):
    provider = provider_factory()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(PlanningService(provider, validator))),
        base_url="http://127.0.0.1",
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert '<label for="intent">Intent</label>' in response.text
    assert 'fetch("/api/plans"' in response.text
    assert provider.calls == 0

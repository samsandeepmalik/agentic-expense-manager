"""run_to_completion must never fabricate a success-sounding reply when the
model produced no final text — that's the exact bug behind 'WhatsApp said
done but nothing was recorded' (empty text silently became the literal
string 'Done.', regardless of whether any tool ran or succeeded)."""
import pytest

from app.agent import runtime


class _FakeSession:
    def __init__(self, events):
        self._events = events

    async def run(self, text):
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_empty_final_text_does_not_claim_done():
    session = _FakeSession([{"type": "done", "text": "", "error": None}])
    reply = await runtime.run_to_completion(session, "add $20 coffee")
    assert reply != "Done."
    assert "sorry" in reply.lower() or "didn't" in reply.lower()


@pytest.mark.asyncio
async def test_normal_final_text_passes_through():
    session = _FakeSession([{"type": "done", "text": "Recorded #42.", "error": None}])
    reply = await runtime.run_to_completion(session, "add $20 coffee")
    assert reply == "Recorded #42."


@pytest.mark.asyncio
async def test_error_reported_takes_precedence():
    session = _FakeSession([{"type": "done", "text": "", "error": "boom"}])
    reply = await runtime.run_to_completion(session, "add $20 coffee")
    assert reply == "Sorry, something went wrong: boom"

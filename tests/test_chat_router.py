import json
import unittest
from unittest.mock import MagicMock, patch

from chat.router import classify_intent, rewrite_follow_up


def _mock_response(status_code: int = 200, response_text: str = ""):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = {"response": response_text}
    mock.text = response_text
    return mock


def _mock_llm(monkeypatch_target, response_text: str, status_code: int = 200):
    """Patches chat.llm_client.requests.post (the shared HTTP-calling helper
    chat.router imports call_llm from) to return response_text as the raw
    "response" field, and TokenManager.get_token so no real OAuth call ever
    happens — keeps these genuinely offline unit tests, not live-API tests."""
    return (
        patch("chat.llm_client.requests.post", return_value=_mock_response(status_code, response_text)),
        patch("chat.llm_client.TokenManager.get_token", return_value="fake-token"),
    )


class ClassifyIntentHappyPathTests(unittest.TestCase):
    """15 example messages, 3 per intent — each mocks the LLM to return the
    expected classification and asserts classify_intent() parses it correctly.
    This tests classify_intent's plumbing/contract (does it correctly surface
    a well-formed model response as {intent, confidence}), not the live
    model's actual judgement — that would be a live/integration test, not a
    unit test, and would make this suite slow and non-deterministic."""

    LEGAL_REFERENCE_MESSAGES = [
        "What is Section 302 of the IPC?",
        "What's the punishment for theft under BNS?",
        "Explain the difference between culpable homicide and murder.",
    ]
    CASE_LOOKUP_MESSAGES = [
        "Summarize crime number 100091036201900002",
        "Who is the accused in case 100011004202000001?",
        "Give me details on FIR 100081029202500002",
    ]
    AGGREGATE_QUERY_MESSAGES = [
        "How many murder cases were registered this year?",
        "What is the average theft amount reported?",
        "Show me the total number of pending cases",
    ]
    FOLLOW_UP_MESSAGES = [
        "How old are they?",
        "What about the other one?",
        "Was he arrested too?",
    ]
    OUT_OF_SCOPE_MESSAGES = [
        "What's the weather like today?",
        "Tell me a joke",
        "Can you help me write a poem about flowers?",
    ]

    def _assert_classifies_as(self, message: str, expected_intent: str, confidence: float = 0.9):
        llm_json = json.dumps({"intent": expected_intent, "confidence": confidence})
        post_patch, token_patch = _mock_llm(self, llm_json)
        with post_patch, token_patch:
            result = classify_intent(message, recent_history=[])
        self.assertEqual(result["intent"], expected_intent, f"for message: {message!r}")
        self.assertAlmostEqual(result["confidence"], confidence)

    def test_legal_reference_messages(self):
        for msg in self.LEGAL_REFERENCE_MESSAGES:
            self._assert_classifies_as(msg, "LEGAL_REFERENCE")

    def test_case_lookup_messages(self):
        for msg in self.CASE_LOOKUP_MESSAGES:
            self._assert_classifies_as(msg, "CASE_LOOKUP")

    def test_aggregate_query_messages(self):
        for msg in self.AGGREGATE_QUERY_MESSAGES:
            self._assert_classifies_as(msg, "AGGREGATE_QUERY")

    def test_follow_up_messages(self):
        recent = [{"Role": "user", "Message": "Who is the accused?"}, {"Role": "assistant", "Message": "Accused Person-49, 45 years old."}]
        for msg in self.FOLLOW_UP_MESSAGES:
            llm_json = json.dumps({"intent": "FOLLOW_UP", "confidence": 0.85})
            post_patch, token_patch = _mock_llm(self, llm_json)
            with post_patch, token_patch:
                result = classify_intent(msg, recent_history=recent)
            self.assertEqual(result["intent"], "FOLLOW_UP", f"for message: {msg!r}")

    def test_out_of_scope_messages(self):
        for msg in self.OUT_OF_SCOPE_MESSAGES:
            self._assert_classifies_as(msg, "OUT_OF_SCOPE")


class ClassifyIntentRobustnessTests(unittest.TestCase):
    """Edge cases classify_intent must degrade safely on — every one of these
    must return intent=None (the caller's signal to fall back to the
    pre-routing single-RAG-path behavior) rather than crash or silently guess
    a category."""

    def test_garbage_non_json_response_defaults_to_none(self):
        post_patch, token_patch = _mock_llm(self, "I'm not sure what you mean.")
        with post_patch, token_patch:
            result = classify_intent("some message")
        self.assertIsNone(result["intent"])
        self.assertEqual(result["confidence"], 0.0)

    def test_unknown_intent_label_defaults_to_none(self):
        llm_json = json.dumps({"intent": "SOMETHING_MADE_UP", "confidence": 0.9})
        post_patch, token_patch = _mock_llm(self, llm_json)
        with post_patch, token_patch:
            result = classify_intent("some message")
        self.assertIsNone(result["intent"])

    def test_confidence_out_of_range_defaults_to_none(self):
        llm_json = json.dumps({"intent": "CASE_LOOKUP", "confidence": 1.7})
        post_patch, token_patch = _mock_llm(self, llm_json)
        with post_patch, token_patch:
            result = classify_intent("some message")
        self.assertIsNone(result["intent"])

    def test_json_wrapped_in_markdown_fence_still_parses(self):
        llm_json = "```json\n" + json.dumps({"intent": "LEGAL_REFERENCE", "confidence": 0.8}) + "\n```"
        post_patch, token_patch = _mock_llm(self, llm_json)
        with post_patch, token_patch:
            result = classify_intent("What is Section 420?")
        self.assertEqual(result["intent"], "LEGAL_REFERENCE")

    def test_think_prefixed_response_still_parses(self):
        inner = json.dumps({"intent": "CASE_LOOKUP", "confidence": 0.75})
        raw = f"<think>reasoning about the message here</think>{inner}"
        post_patch, token_patch = _mock_llm(self, raw)
        with post_patch, token_patch:
            result = classify_intent("crime number 100091036201900002")
        self.assertEqual(result["intent"], "CASE_LOOKUP")

    def test_non_200_http_status_defaults_to_none(self):
        post_patch, token_patch = _mock_llm(self, "", status_code=500)
        with post_patch, token_patch:
            result = classify_intent("some message")
        self.assertIsNone(result["intent"])

    def test_network_exception_defaults_to_none(self):
        with patch("chat.llm_client.requests.post", side_effect=Exception("connection reset")), \
             patch("chat.llm_client.TokenManager.get_token", return_value="fake-token"):
            result = classify_intent("some message")
        self.assertIsNone(result["intent"])


class RewriteFollowUpTests(unittest.TestCase):
    def test_empty_history_returns_message_unchanged_without_calling_llm(self):
        with patch("chat.llm_client.requests.post") as mock_post:
            result = rewrite_follow_up("How old are they?", "")
        self.assertEqual(result, "How old are they?")
        mock_post.assert_not_called()

    def test_successful_rewrite_returns_llm_output(self):
        post_patch, token_patch = _mock_llm(self, "How old is Accused Person-49?")
        with post_patch, token_patch:
            result = rewrite_follow_up("How old are they?", "user: Who is the accused?\nassistant: Accused Person-49.")
        self.assertEqual(result, "How old is Accused Person-49?")

    def test_failed_rewrite_falls_back_to_original_message(self):
        with patch("chat.llm_client.requests.post", side_effect=Exception("timeout")), \
             patch("chat.llm_client.TokenManager.get_token", return_value="fake-token"):
            result = rewrite_follow_up("How old are they?", "some history")
        self.assertEqual(result, "How old are they?")


if __name__ == "__main__":
    unittest.main()

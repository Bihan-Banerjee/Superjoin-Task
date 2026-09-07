"""Response parsing, salvage, caching keys and schema translation."""

import pytest

from app.llm.base import LlmError, LlmRequest, extract_json, salvage_truncated_json
from app.llm.providers import _to_gemini_schema
from app.llm.schemas import FACT_EXTRACTION_SCHEMA


class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_code_fenced(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_around_the_object(self):
        assert extract_json('Here you go: {"b": [1, 2]} hope that helps') == {"b": [1, 2]}

    def test_braces_inside_strings_do_not_confuse_the_scan(self):
        assert extract_json(r'{"q": "he said \"hi\" then {}"}') == {"q": 'he said "hi" then {}'}

    def test_empty_response_is_an_error(self):
        with pytest.raises(LlmError):
            extract_json("   ")

    def test_non_json_is_an_error(self):
        with pytest.raises(LlmError):
            extract_json("I could not complete that request.")


class TestSalvage:
    """A truncated extraction is a long list whose last item is incomplete.

    Discarding the whole response loses every fact that was written correctly, and those
    still have to pass grounding afterwards, so keeping them costs no accuracy.
    """

    def test_keeps_the_complete_items_and_drops_the_partial_one(self):
        truncated = (
            '{"facts": [{"subject": "A", "value_text": "1"}, '
            '{"subject": "B", "value_text": "2"}, {"subject": "C", "val'
        )
        salvaged = salvage_truncated_json(truncated)
        assert salvaged == {
            "facts": [{"subject": "A", "value_text": "1"}, {"subject": "B", "value_text": "2"}]
        }

    def test_returns_nothing_when_no_item_completed(self):
        assert salvage_truncated_json('{"facts": [{"subj') is None

    def test_leaves_valid_json_alone(self):
        assert salvage_truncated_json('{"facts": [{"a": 1}]}') == {"facts": [{"a": 1}]}

    def test_a_brace_inside_a_truncated_string_does_not_break_the_walk(self):
        truncated = '{"facts": [{"quote": "revenue was {8,142} in FY24"}, {"quote": "part'
        salvaged = salvage_truncated_json(truncated)
        assert salvaged is not None
        assert len(salvaged["facts"]) == 1

    def test_handles_a_bare_array(self):
        assert salvage_truncated_json('[{"a": 1}, {"b') == [{"a": 1}]

    def test_returns_nothing_for_text_that_is_not_json(self):
        assert salvage_truncated_json("no json here at all") is None


class TestGeminiSchema:
    """Gemini rejects several standard JSON Schema keywords and wants upper-case types.

    Sending the schema unmodified fails the whole request, so it is rewritten at the provider
    boundary rather than every prompt being written twice.
    """

    def test_types_are_upper_cased_and_structure_is_preserved(self):
        translated = _to_gemini_schema(FACT_EXTRACTION_SCHEMA)
        assert translated["type"] == "OBJECT"
        assert set(translated["properties"]) == {"facts", "unattributed"}
        assert translated["properties"]["facts"]["type"] == "ARRAY"
        assert translated["properties"]["facts"]["items"]["type"] == "OBJECT"

    def test_unsupported_keywords_are_dropped(self):
        translated = _to_gemini_schema(
            {
                "type": "object",
                "additionalProperties": False,
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "properties": {"a": {"type": "string"}},
            }
        )
        assert "additionalProperties" not in translated
        assert "$schema" not in translated
        assert translated["properties"]["a"]["type"] == "STRING"

    def test_enums_and_descriptions_survive(self):
        translated = _to_gemini_schema(
            {"type": "string", "enum": ["a", "b"], "description": "keep me"}
        )
        assert translated["enum"] == ["a", "b"]
        assert translated["description"] == "keep me"


class TestCacheKey:
    def test_identical_requests_share_a_key(self):
        request = LlmRequest(purpose="extract", system="s", user="u", schema={"type": "object"})
        assert request.cache_key("gemini", "m") == request.cache_key("gemini", "m")

    def test_a_different_model_is_a_different_key(self):
        request = LlmRequest(purpose="extract", system="s", user="u", schema={"type": "object"})
        assert request.cache_key("gemini", "m") != request.cache_key("gemini", "n")

    def test_a_different_prompt_is_a_different_key(self):
        schema = {"type": "object"}
        left = LlmRequest(purpose="extract", system="s", user="page one", schema=schema)
        right = LlmRequest(purpose="extract", system="s", user="page two", schema=schema)
        assert left.cache_key("gemini", "m") != right.cache_key("gemini", "m")

    def test_images_participate_in_the_key(self):
        schema = {"type": "object"}
        without = LlmRequest(purpose="extract", system="s", user="u", schema=schema)
        with_image = LlmRequest(
            purpose="extract", system="s", user="u", schema=schema, images=[b"png-bytes"]
        )
        assert without.cache_key("gemini", "m") != with_image.cache_key("gemini", "m")

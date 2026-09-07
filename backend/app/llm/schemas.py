"""JSON schemas for every structured model call.

Kept in one place because they are a contract in two directions: the provider constrains
decoding against them, and the pipeline validates against them before touching the
database. Both sides have to agree, and both change together.
"""

from __future__ import annotations

from typing import Any

FACT_KINDS = ["quantitative", "categorical", "temporal", "relational", "definitional"]

BASIS_VALUES = [
    "actual",
    "estimate",
    "projection",
    "forecast",
    "revised",
    "restated",
    "pro_forma",
    "budgeted",
    "target",
    "provisional",
    "unspecified",
]

VERDICTS = [
    "corroborates",
    "contradicts",
    "reconciled_by_context",
    "refines",
    "unrelated",
]

DIMENSIONS = [
    "period",
    "unit_scale",
    "currency",
    "scope",
    "segment",
    "basis",
    "vintage",
    "entity",
    "definition",
    "none",
]


DOCUMENT_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Document title as printed."},
        "publisher": {
            "type": "string",
            "description": "Organisation that issued the document.",
        },
        "doc_type": {
            "type": "string",
            "description": (
                "Short lowercase label for the kind of document, e.g. annual_report, "
                "prospectus, earnings_presentation, staff_report, statistical_review."
            ),
        },
        "subject_entity": {
            "type": "string",
            "description": "Primary entity the document is about.",
        },
        "as_of_date": {
            "type": "string",
            "description": "ISO date the data is stated as of, or empty if not stated.",
        },
        "published_date": {
            "type": "string",
            "description": "ISO publication date, or empty if not stated.",
        },
        "period_label": {
            "type": "string",
            "description": "Main reporting period covered, exactly as the document writes it.",
        },
        "default_currency": {
            "type": "string",
            "description": "ISO currency code figures default to, or empty.",
        },
        "default_scale": {
            "type": "string",
            "description": (
                "Scale word figures default to, e.g. million, crore, thousand. Empty if "
                "figures are stated in whole units or no default is declared."
            ),
        },
        "fiscal_convention": {
            "type": "string",
            "enum": ["india", "calendar", "us_federal", "uk"],
            "description": (
                "Which fiscal year the document uses. 'india' means April to March, so "
                "FY24 and 2023-24 both end on 31 March 2024."
            ),
        },
        "reporting_basis": {
            "type": "string",
            "description": "e.g. consolidated, standalone, group, mixed, or empty.",
        },
        "language": {"type": "string"},
        "notes": {
            "type": "string",
            "description": "Anything about scope or conventions a reader must know.",
        },
    },
    "required": ["title", "doc_type", "fiscal_convention"],
}


_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "statement": {
            "type": "string",
            "description": "The fact in one self-contained sentence, readable out of context.",
        },
        "kind": {"type": "string", "enum": FACT_KINDS},
        "subject": {
            "type": "string",
            "description": "The entity the fact is about, named as the document names it.",
        },
        "predicate": {
            "type": "string",
            "description": (
                "What is measured or asserted, as a short noun phrase without the subject, "
                "the period or the value. For example 'revenue from services', "
                "'headline inflation', 'registered office address'."
            ),
        },
        "value_text": {
            "type": "string",
            "description": "The value exactly as printed, including its symbols.",
        },
        "value_number": {
            "type": "number",
            "description": "Numeric value with grouping removed. Omit for non-numeric facts.",
        },
        "unit": {
            "type": "string",
            "description": (
                "Unit as written or as declared for the page, e.g. '%', 'Cr', 'million', "
                "'shipments', 'days'. Empty if the value is not a measurement."
            ),
        },
        "currency": {"type": "string", "description": "ISO currency code, or empty."},
        "period": {
            "type": "string",
            "description": (
                "Period the value covers, exactly as the document writes it, e.g. 'FY24', "
                "'2024-25', 'Q4 FY24', 'as at March 31, 2024'. Empty if timeless."
            ),
        },
        "qualifiers": {
            "type": "object",
            "description": (
                "Any conditions that change what the value means: segment, geography, "
                "basis of consolidation, measurement definition, counterparty. Use short "
                "lowercase keys. Omit anything the document does not state."
            ),
            "properties": {
                "segment": {"type": "string"},
                "geography": {"type": "string"},
                "scope": {"type": "string"},
                "definition": {"type": "string"},
                "counterparty": {"type": "string"},
            },
        },
        "basis": {"type": "string", "enum": BASIS_VALUES},
        "evidence_quote": {
            "type": "string",
            "description": (
                "A span copied character for character from the page content given to you, "
                "containing the value and enough words to identify what it measures."
            ),
        },
        "confidence": {"type": "number", "description": "Between 0 and 1."},
    },
    "required": ["statement", "kind", "subject", "predicate", "value_text", "evidence_quote"],
}


FACT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": _FACT_SCHEMA},
        "unattributed": {
            "type": "array",
            "description": (
                "Values visible on the page that you could not confidently attach to a "
                "subject, measure or period. Report them rather than guessing."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "value_text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["value_text", "reason"],
            },
        },
    },
    "required": ["facts"],
}


ADJUDICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "dimension": {
            "type": "string",
            "enum": DIMENSIONS,
            "description": "Which contextual dimension accounts for the difference.",
        },
        "explanation": {
            "type": "string",
            "description": (
                "One or two sentences. Say what in the two pieces of evidence supports "
                "this verdict. Refer to what the documents state, not to general knowledge."
            ),
        },
        "reconciliation": {
            "type": "string",
            "description": ("If both statements can be true at once, how. Empty when they cannot."),
        },
        "confidence": {"type": "number"},
    },
    "required": ["verdict", "dimension", "explanation", "confidence"],
}


MEASURE_LINKING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "surface": {
                        "type": "string",
                        "description": "The candidate phrase being resolved, copied back exactly.",
                    },
                    "action": {"type": "string", "enum": ["link", "create"]},
                    "measure": {
                        "type": "string",
                        "description": (
                            "For 'link', the existing canonical name exactly as given. "
                            "For 'create', a new canonical name in lowercase words."
                        ),
                    },
                    "description": {"type": "string"},
                },
                "required": ["surface", "action", "measure"],
            },
        }
    },
    "required": ["decisions"],
}

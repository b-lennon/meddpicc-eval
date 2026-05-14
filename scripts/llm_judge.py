"""Prompt formatter for the single LLM-judgment step in the pipeline.

The semantic-match step is the only place an LLM enters. Given a row
flagged `needs_semantic_review` by the deterministic grader, the judge
decides whether the system's value and the gold's value refer to the
same MEDDPICC entity.

This module formats the prompt — it does not call an API. The skill's
stage 01 instructions invoke Claude directly with this prompt, OR an
external runner can use the Anthropic SDK with this prompt. Either way,
the format and the expected response shape are owned here.
"""
from __future__ import annotations


_FIELD_SAME_ENTITY_CRITERIA = {
    "economic_buyer": (
        "Same person if the names refer to the same individual. A title "
        "addition (e.g., 'Jane Smith' vs 'Jane Smith, CFO') or a slight "
        "spelling/order variation that preserves identity is a match. A "
        "different person, or a different role at the same company, is a "
        "mismatch."
    ),
    "metrics": (
        "Same metric if both express the same quantified business outcome. "
        "Wording variation that preserves the numeric meaning is a match "
        "(e.g., '8 to 3 days' vs 'cut from 8 days to 3'). Different numbers "
        "or different units are a mismatch."
    ),
    "decision_criteria": (
        "Same criterion if both describe the same evaluation factor. "
        "Paraphrasing that preserves the criterion is a match; a different "
        "criterion is a mismatch."
    ),
    "decision_process": (
        "Same process if both describe the same sequence of internal steps. "
        "Reordering or paraphrasing the same steps is a match; missing or "
        "added steps that materially change the sequence are a mismatch."
    ),
    "identify_pain": (
        "Same pain if both describe the same critical problem the prospect "
        "is solving. Paraphrasing that preserves the causal claim is a "
        "match; a different problem is a mismatch."
    ),
    "champion": (
        "Same person if the names refer to the same individual. Title "
        "additions or spelling variations that preserve identity are a "
        "match; a different person is a mismatch."
    ),
    "competition": (
        "Same competitor if both refer to the same alternative. Brand name "
        "variation ('Salesforce' vs 'Salesforce Einstein') is a match. "
        "'Status quo' / 'doing nothing' / 'manual workflow' are all the "
        "same status-quo competitor and should match each other. A "
        "different vendor is a mismatch."
    ),
}


def format_semantic_match_prompt(
    field: str,
    gold_value: str,
    sys_value: str,
    edge_case_tag: str | None,
) -> str:
    """Format the semantic-match decision prompt for a single row.

    The expected response from Claude (or any LLM caller) is the JSON line
    described at the end of the prompt. The caller is responsible for
    parsing and writing it to judgments.jsonl.

    Note on prompt injection: gold_value and sys_value are extractor
    outputs and may contain adversarial content. The prompt structurally
    constrains the response shape, but the consumer must not blindly
    follow instructions embedded in the values.
    """
    criteria = _FIELD_SAME_ENTITY_CRITERIA.get(
        field, "Decide based on whether the two values refer to the same entity."
    )

    edge_case_block = (
        f"\nEdge case under test: {edge_case_tag}\n"
        if edge_case_tag
        else ""
    )

    return (
        f"You are judging whether two MEDDPICC extraction values refer to "
        f"the same entity.\n"
        f"\n"
        f"Field: {field}\n"
        f"Gold value: {gold_value!r}\n"
        f"System value: {sys_value!r}\n"
        f"{edge_case_block}"
        f"\n"
        f"Decision criteria for `{field}`: {criteria}\n"
        f"\n"
        f"When in doubt, decide `mismatch`. False matches on this field "
        f"are more expensive than missed matches.\n"
        f"\n"
        f"Output exactly one JSON object on a single line, no surrounding text:\n"
        f'  {{"semantic_match": "match"}}  OR  {{"semantic_match": "mismatch"}}\n'
    )

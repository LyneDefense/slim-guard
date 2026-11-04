import pytest
from pydantic import ValidationError

from slim_guard.expression_style.package import StylePackage
from slim_guard.expression_style.trainer.construction_contracts import (
    ExampleSelection,
    GuideDraft,
    RuleDraft,
)
from slim_guard.expression_style.trainer.construction_evidence import EvidenceCatalog
from slim_guard.expression_style.trainer.contracts import Material, MaterialAnalysis


def catalog_with(*roles, original="不用客气，很高兴帮到你。", desired="不客气。"):
    materials = tuple(
        Material(
            id=f"source-{i}",
            revision=i + 2,
            user_input="谢谢",
            original_response=original,
            desired_response=desired,
            correction_opinion="不要客套",
        )
        for i in range(len(roles))
    )
    analyses = tuple(
        MaterialAnalysis(example_id=m.id, role=role, reason="测试分析")
        for m, role in zip(materials, roles, strict=True)
    )
    return EvidenceCatalog(materials, analyses), materials


def draft(*refs):
    return GuideDraft(
        summary="自然直接",
        rules=(
            RuleDraft(
                rule_id="concise", text="少用客套", boundary="不改变事实", evidence_refs=refs
            ),
        ),
    )


def test_catalog_binds_exact_field_revision_offsets_and_package_hash():
    catalog, materials = catalog_with("positive")
    guide = draft("e2", "e3")
    assert catalog.guide_validation(("e2", "e3")).check(guide) == []
    selection = ExampleSelection(example_ids=("m1",), explanation="最小互补集合")
    package = catalog.package(guide, selection, "doctor", "v", "医生")
    assert StylePackage.model_validate_json(package.model_dump_json()) == package
    assert package.examples[0].id == materials[0].id
    assert package.examples[0].rule_ids == ("concise",)
    for binding, evidence in zip(
        (catalog.evidence["e2"], catalog.evidence["e3"]),
        package.guide.rules[0].evidence,
        strict=True,
    ):
        assert evidence.revision == materials[0].revision
        assert evidence.example_id == materials[0].id
        assert evidence.quote == getattr(materials[0], binding.field)[binding.start : binding.end]
    assert catalog.artifact()[1]["field"] == "desired_response"
    assert "desired_response[0:4]" in catalog.guide_validation(("e2",)).references["e2"]


def test_chunks_are_verbatim_bounded_and_cover_all_non_whitespace_characters():
    original = "甲" * 999 + "  " + "乙" * 1200
    catalog, materials = catalog_with("negative", original=original, desired="")
    chunks = [e for e in catalog.evidence.values() if e.field == "original_response"]
    assert len(chunks) == 3
    assert all(0 < len(e.quote) <= 1000 for e in chunks)
    assert all(e.quote == materials[0].original_response[e.start : e.end] for e in chunks)
    assert "".join(e.quote for e in chunks).replace(" ", "") == original.replace(" ", "")


@pytest.mark.parametrize(
    "role",
    [
        "out_of_scope",
        "content_change",
        "semantic_drift",
        "duplicate",
        "conflict",
        "missing_context",
        "unusable",
    ],
)
def test_excluded_material_never_enters_model_payload_or_evidence_pool(role):
    catalog, materials = catalog_with("positive", role)
    payload = catalog.payload(catalog.materials)
    assert len(payload["materials"]) == 1
    assert {e.example_id for e in catalog.evidence.values()} == {materials[0].id}
    assert (
        materials[1].id
        not in catalog.selection_validation(draft("e1"), "doctor", "v", "医生").references.values()
    )


def test_negative_evidence_is_allowed_but_never_a_positive_example():
    catalog, _ = catalog_with("negative")
    assert catalog.guide_validation(("e3",)).check(draft("e3")) == []
    assert catalog.eligible_examples(draft("e3")) == {}
    selection = ExampleSelection(example_ids=("m1",), explanation="不能作为正例")
    errors = catalog.selection_validation(draft("e3"), "doctor", "v", "医生").check(selection)
    assert errors[0]["type"] == "unsupported_example"


def test_evidence_reference_must_belong_to_this_call_not_another_batch():
    catalog, _ = catalog_with("positive", "positive")
    errors = catalog.guide_validation(("e1", "e2", "e3")).check(draft("e4"))
    assert errors[0]["path"] == "rules.0.evidence_refs.0"
    assert errors[0]["type"] == "unknown_evidence"
    # Merge may only reuse references actually selected by its input guides.
    used = catalog.used_refs((draft("e1"), draft("e4")))
    assert catalog.guide_validation(used).check(draft("e2"))[0]["type"] == "unknown_evidence"


def test_duplicate_rule_and_evidence_errors_have_field_locations():
    catalog, _ = catalog_with("positive")
    value = draft("e1", "e1")
    value = value.model_copy(update={"rules": value.rules * 2})
    errors = catalog.guide_validation(("e1",)).check(value)
    assert {e["type"] for e in errors} == {"duplicate_rule", "duplicate_evidence"}
    assert {e["path"] for e in errors} == {
        "rules.0.evidence_refs.1",
        "rules.1.rule_id",
        "rules.1.evidence_refs.1",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote", "原回答和期望回答拼接的伪引文"),
        ("evidence", [{"example_id": "preview:1", "revision": 1, "quote": "伪引文"}]),
        ("revision", 99),
    ],
)
def test_model_cannot_write_provenance_fields(field, value):
    data = draft("e1").model_dump(mode="json")
    data["rules"][0][field] = value
    with pytest.raises(ValidationError) as failure:
        GuideDraft.model_validate(data)
    assert failure.value.errors()[0]["type"] == "extra_forbidden"


def test_selection_cannot_rewrite_guide_or_choose_unlinked_and_preview_sources():
    catalog, _ = catalog_with("positive", "positive")
    validation = catalog.selection_validation(draft("e1"), "doctor", "v", "医生")
    selection = ExampleSelection(
        example_ids=("m2", "preview:case", "m1", "m1"), explanation="错误选择"
    )
    assert [e["type"] for e in validation.check(selection)] == [
        "unsupported_example",
        "unsupported_example",
        "duplicate_example",
    ]
    with pytest.raises(ValidationError):
        ExampleSelection.model_validate({**selection.model_dump(), "guide": {}})


def test_prompt_budget_failure_returns_repairable_field_error(monkeypatch):
    catalog, _ = catalog_with("positive", original="原" * 4000, desired="期" * 4000)
    value = GuideDraft(
        summary="长" * 1000,
        rules=tuple(
            RuleDraft(rule_id=f"r{i}", text="规" * 500, boundary="界" * 500, evidence_refs=("e1",))
            for i in range(10)
        ),
    )
    error = catalog.guide_validation(("e1",)).check(value)[0]
    assert error["type"] == "prompt_budget" and error["path"] == "rules"
    selection = ExampleSelection(example_ids=("m1",), explanation="选择")
    monkeypatch.setattr("slim_guard.expression_style.package.MAX_PROMPT_BYTES", 1000)
    error = catalog.selection_validation(draft("e1"), "doctor", "v", "医生").check(selection)[0]
    assert error["type"] == "package_budget" and error["path"] == "example_ids"


@pytest.mark.parametrize("case", ["missing", "duplicate_material", "duplicate_analysis"])
def test_catalog_rejects_non_unique_material_analysis_coverage(case):
    _, materials = catalog_with("positive", "positive")
    analyses = tuple(
        MaterialAnalysis(example_id=m.id, role="positive", reason="分析") for m in materials
    )
    if case == "missing":
        analyses = analyses[:1]
    elif case == "duplicate_material":
        materials = materials[:1] * 2
    else:
        analyses = analyses[:1] * 2
    with pytest.raises(ValueError, match="唯一对应"):
        EvidenceCatalog(materials, analyses)

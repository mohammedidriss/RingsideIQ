"""The personal-data check every dataset export passes (common.privacy, ADR-014)."""

from __future__ import annotations

import pytest

from common.privacy import PersonalDataError, check_no_personal_data, find_personal_data


def test_a_personal_field_is_found_however_it_is_spelled_and_nested() -> None:
    record = {"take": {"labels": [{"t_ms": 10, "Full Name": "x"}]}, "fighterName": "y", "email": ""}
    found = find_personal_data(record)
    assert any("Full Name" in f for f in found)
    assert any("fighterName" in f for f in found)
    assert any(".email" in f for f in found)


def test_a_known_name_or_email_is_found_in_values_keys_and_paths() -> None:
    known = ["Sami Haddad", "sami@example.org"]
    assert find_personal_data({"note": "jab by SAMI HADDAD"}, known)
    assert find_personal_data({"sami haddad's take": 1}, known)
    assert find_personal_data(["contact sami@example.org"], known)
    assert find_personal_data("takes/sami_haddad/x", ["sami"])  # "_" doesn't join words


def test_a_name_inside_another_word_is_not_a_match() -> None:
    assert find_personal_data({"stance": "bottom", "block": "custom"}, ["Tom"]) == []
    assert find_personal_data({"x": "Al"}, ["Al"]) == []  # too short to look for


def test_clean_research_data_passes() -> None:
    manifest = {
        "takes": [{"take_id": "t1", "participant": "F-0001", "stance": "orthodox"}],
        "splits": {"train": [], "test": []},
        "counts": {"takes": 1, "fighters": 1},
    }
    assert find_personal_data(manifest, ["Sami Haddad"]) == []
    check_no_personal_data(manifest, ["Sami Haddad"])


def test_check_raises_with_the_findings() -> None:
    with pytest.raises(PersonalDataError) as e:
        check_no_personal_data({"dob": "1990-01-01"}, where="take.json")
    assert e.value.findings == ["take.json.dob: personal-data field"]

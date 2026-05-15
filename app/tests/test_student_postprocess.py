import pytest

from app.student_model.student_postprocess import (
    clean_text,
    normalize_object_label,
    normalize_color_label,
    extract_requested_object,
    extract_requested_color,
    looks_like_greeting,
    wants_recolor,
    wants_remove,
    normalize_student_plan,
)


def test_clean_text_basic():
    assert clean_text("  Bonjour !!! ") == "bonjour"
    assert clean_text("Change la couleur, STP.") == "change la couleur stp"


def test_normalize_object_label_french_aliases():
    assert normalize_object_label("la moto") == "motorcycle"
    assert normalize_object_label("casque") == "helmet"
    assert normalize_object_label("veste") == "jacket"
    assert normalize_object_label("personne") == "person"


def test_normalize_object_label_english_aliases():
    assert normalize_object_label("motorbike") == "motorcycle"
    assert normalize_object_label("helmet") == "helmet"
    assert normalize_object_label("gloves") == "gloves"
    assert normalize_object_label("human") == "person"


def test_normalize_object_label_article_removal():
    assert normalize_object_label("une veste") == "jacket"
    assert normalize_object_label("un casque") == "helmet"
    assert normalize_object_label("les gants") == "gloves"


def test_normalize_color_label_french_and_hex():
    assert normalize_color_label("bleue") == "blue"
    assert normalize_color_label("rouge") == "red"
    assert normalize_color_label("#0000ff") == "blue"
    assert normalize_color_label("#ff69b4") == "pink"


def test_extract_requested_object_simple():
    assert extract_requested_object("supprime la moto") == "motorcycle"
    assert extract_requested_object("retire le casque") == "helmet"
    assert extract_requested_object("change la couleur de la veste") == "jacket"


def test_extract_requested_object_person():
    assert extract_requested_object("supprime la personne") == "person"
    assert extract_requested_object("remove the human") == "person"


def test_extract_requested_object_none():
    assert extract_requested_object("bonjour comment ça va") is None
    assert extract_requested_object("augmente le contraste") is None


def test_extract_requested_color_simple():
    assert extract_requested_color("mets la veste en bleu") == "blue"
    assert extract_requested_color("change en rouge") == "red"
    assert extract_requested_color("mets en rose") == "pink"


def test_extract_requested_color_none():
    assert extract_requested_color("supprime la moto") is None
    assert extract_requested_color("bonjour") is None


def test_looks_like_greeting():
    assert looks_like_greeting("salut") is True
    assert looks_like_greeting("bonjour") is True
    assert looks_like_greeting("hello") is True
    assert looks_like_greeting("supprime la moto") is False


def test_wants_recolor():
    assert wants_recolor("change la couleur de la veste") is True
    assert wants_recolor("mets la veste en bleu") is True
    assert wants_recolor("recolor the helmet") is True
    assert wants_recolor("supprime la moto") is False


def test_wants_remove():
    assert wants_remove("supprime la moto") is True
    assert wants_remove("enlève le casque") is True
    assert wants_remove("retire la personne") is True
    assert wants_remove("remove the person") is True
    assert wants_remove("change la couleur") is False


def test_normalize_student_plan_invalid_model_output():
    out = normalize_student_plan("not a dict", "supprime la moto")
    assert out["mode"] == "chat"
    assert "invalide" in out["text"].lower()


def test_normalize_student_plan_greeting():
    out = normalize_student_plan({"mode": "chat", "text": "x"}, "bonjour")
    assert out["mode"] == "chat"
    assert "bonjour" in out["text"].lower()


def test_normalize_student_plan_ask_for_missing_color():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "object.recolor",
                    "params": {"object": "veste"}
                }
            ]
        }
    }

    out = normalize_student_plan(dm_out, "change la couleur de la veste")
    assert out["mode"] == "ask"
    assert out["slot"] == "color"


def test_normalize_student_plan_fill_missing_remove_object():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "object.remove",
                    "params": {}
                }
            ]
        }
    }

    out = normalize_student_plan(dm_out, "supprime la moto")
    assert out["mode"] == "plan"
    action = out["plan"]["actions"][0]
    assert action["action"] == "object.remove"
    assert action["params"]["object"] == "motorcycle"


def test_normalize_student_plan_remove_id_to_object():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "object.remove",
                    "params": {"id": "casque"}
                }
            ]
        }
    }

    out = normalize_student_plan(dm_out, "retire le casque")
    action = out["plan"]["actions"][0]
    assert action["params"]["object"] == "helmet"
    assert "id" not in action["params"]


def test_normalize_student_plan_recolor_normalization():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "object.recolor",
                    "params": {
                        "object": "veste",
                        "color": "bleue",
                    }
                }
            ]
        }
    }

    out = normalize_student_plan(dm_out, "mets la veste en bleu")
    action = out["plan"]["actions"][0]
    assert action["action"] == "object.recolor"
    assert action["params"]["object"] == "jacket"
    assert action["params"]["color"] == "blue"


def test_normalize_student_plan_recolor_add_missing_object_and_color():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "object.recolor",
                    "params": {}
                }
            ]
        }
    }

    out = normalize_student_plan(dm_out, "mets la veste en bleu")
    action = out["plan"]["actions"][0]
    assert action["params"]["object"] == "jacket"
    assert action["params"]["color"] == "blue"


def test_normalize_student_plan_blur_rewritten_into_recolor():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "effect.blur",
                    "params": {"intensity": 30}
                }
            ]
        }
    }

    out = normalize_student_plan(dm_out, "mets la veste en bleu")
    action = out["plan"]["actions"][0]
    assert action["action"] == "object.recolor"
    assert action["params"]["object"] == "jacket"
    assert action["params"]["color"] == "blue"


def test_normalize_student_plan_contrast_rewrite():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "object.recolor",
                    "params": {"contrast": 2}
                }
            ]
        }
    }

    out = normalize_student_plan(dm_out, "augmente le contraste")
    action = out["plan"]["actions"][0]
    assert action["action"] == "color.contrast"
    assert action["params"]["level"] == "increase"


def test_normalize_student_plan_non_plan_passthrough():
    dm_out = {"mode": "chat", "text": "ok"}
    out = normalize_student_plan(dm_out, "test")
    assert out == dm_out


@pytest.mark.parametrize(
    ("instruction", "expected_action", "expected_params"),
    [
        (
            "rendre l'image noir et blanc",
            "filter.black_white",
            {"method": "desaturate"},
        ),
        (
            "désature l’image",
            "filter.black_white",
            {"method": "desaturate"},
        ),
        (
            "augmente la luminosité",
            "color.brightness",
            {"level": "increase", "amount": 40},
        ),
        (
            "augmente le contraste",
            "color.contrast",
            {"level": "increase", "amount": 40},
        ),
    ],
)
def test_normalize_student_plan_global_adjustment_overrides(instruction, expected_action, expected_params):
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "object.recolor",
                    "params": {"color_mode": "grayscale"},
                }
            ]
        },
    }

    out = normalize_student_plan(dm_out, instruction)

    assert out["mode"] == "plan"
    action = out["plan"]["actions"][0]
    assert action["action"] == expected_action
    assert action["params"] == expected_params


def test_normalize_student_plan_global_darken_override():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "effect.blur",
                    "params": {"intensity": 30},
                }
            ]
        },
    }

    out = normalize_student_plan(dm_out, "diminue la luminosité")

    action = out["plan"]["actions"][0]
    assert action["action"] == "color.brightness"
    assert action["params"] == {"level": "decrease", "amount": 40}


def test_normalize_student_plan_keeps_global_blur():
    dm_out = {
        "mode": "plan",
        "plan": {
            "actions": [
                {
                    "action": "effect.blur",
                    "params": {"intensity": 70},
                }
            ]
        },
    }

    out = normalize_student_plan(dm_out, "floute l’image")

    action = out["plan"]["actions"][0]
    assert action["action"] == "effect.blur"
    assert action["params"] == {"intensity": 70}

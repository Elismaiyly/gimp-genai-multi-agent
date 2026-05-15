from app.executor.ir_translator import IRTranslator


def test_translate_object_remove_normalizes_label_and_instance_strategy():
    translator = IRTranslator()

    result = translator.translate(
        {
            "actions": [
                {
                    "action": "object.remove",
                    "params": {
                        "object": "la moto",
                        "instance": {"strategy": "gauche"},
                    },
                    "notes": "remove bike",
                }
            ]
        }
    )

    assert result == {
        "actions": [
            {
                "action": "object.remove",
                "params": {
                    "object": "motorcycle",
                    "instance": {"strategy": "left"},
                },
                "notes": "remove bike",
            }
        ]
    }


def test_translate_object_remove_strips_apostrophe_article_for_helmet():
    translator = IRTranslator()

    result = translator.translate(
        {
            "actions": [
                {
                    "action": "object.remove",
                    "params": {
                        "object": "l'casque",
                        "instance": {"strategy": "centre"},
                    },
                }
            ]
        }
    )

    translated = result["actions"][0]

    assert translated["action"] == "object.remove"
    assert translated["params"]["object"] == "helmet"
    assert translated["params"]["instance"]["strategy"] == "center"


def test_translate_object_recolor_keeps_color_while_normalizing_object():
    translator = IRTranslator()

    result = translator.translate(
        {
            "actions": [
                {
                    "action": "object.recolor",
                    "params": {
                        "object": "the person",
                        "color": "blue",
                        "instance": {"strategy": "right"},
                    },
                }
            ]
        }
    )

    translated = result["actions"][0]

    assert translated["params"]["object"] == "person"
    assert translated["params"]["color"] == "blue"
    assert translated["params"]["instance"]["strategy"] == "right"


def test_translate_object_recolor_normalizes_motorcycle_and_instance_strategy():
    translator = IRTranslator()

    result = translator.translate(
        {
            "actions": [
                {
                    "action": "object.recolor",
                    "params": {
                        "object": "la moto",
                        "color": "red",
                        "instance": {"strategy": "gauche"},
                    },
                }
            ]
        }
    )

    translated = result["actions"][0]

    assert translated["params"]["object"] == "motorcycle"
    assert translated["params"]["color"] == "red"
    assert translated["params"]["instance"]["strategy"] == "left"


def test_translate_filter_black_white_to_gimp_desaturate():
    translator = IRTranslator()

    result = translator.translate(
        {
            "actions": [
                {
                    "action": "filter.black_white",
                    "params": {"method": "desaturate"},
                }
            ]
        }
    )

    assert result == {
        "actions": [
            {
                "action": "gimp.filter.desaturate",
                "params": {"method": "desaturate"},
                "notes": "Traduit de filter.black_white",
            }
        ]
    }


def test_translate_color_brightness_to_brightness_contrast():
    translator = IRTranslator()

    result = translator.translate(
        {
            "actions": [
                {
                    "action": "color.brightness",
                    "params": {"level": "increase", "amount": 40},
                }
            ]
        }
    )

    assert result == {
        "actions": [
            {
                "action": "gimp.adjust.brightness_contrast",
                "params": {"brightness": 0.4, "contrast": 0.0},
                "notes": "Traduit de color.brightness",
            }
        ]
    }


def test_translate_color_contrast_to_brightness_contrast():
    translator = IRTranslator()

    result = translator.translate(
        {
            "actions": [
                {
                    "action": "color.contrast",
                    "params": {"level": "increase", "amount": 40},
                }
            ]
        }
    )

    assert result == {
        "actions": [
            {
                "action": "gimp.adjust.brightness_contrast",
                "params": {"brightness": 0.0, "contrast": 0.4},
                "notes": "Traduit de color.contrast",
            }
        ]
    }

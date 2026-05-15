from app.executor.gimp_executor import ExecContext, GimpExecutor


def _ctx() -> ExecContext:
    return ExecContext(
        image_path="/tmp/test.png",
        image_b64="ZmFrZQ==",
        image_width=300,
        image_height=200,
        vision_agent={"serviceUrl": "http://vision.test/a2a/invoke"},
    )


def test_compile_object_recolor_motorcycle_uses_refined_mask_and_preserves_instance(monkeypatch):
    executor = GimpExecutor()
    ctx = _ctx()
    captured = {}

    resolved_bbox = {"x": 10, "y": 20, "width": 90, "height": 60}

    def fake_resolve(object_label, _ctx, _dialog_state, instance_sel, vision_overrides=None):
        captured["object_label"] = object_label
        captured["instance_sel"] = instance_sel
        captured["vision_overrides"] = vision_overrides
        return resolved_bbox, "raw-motorcycle-mask"

    def fake_refine(mask_png_b64, object_label="", resolved_bbox=None):
        captured["refine"] = (mask_png_b64, object_label, resolved_bbox)
        return "refined-motorcycle-mask"

    monkeypatch.setattr(executor, "_resolve_instance_mask", fake_resolve)
    monkeypatch.setattr(executor, "_refine_mask_png_b64_for_recolor", fake_refine)

    result = executor._compile_step(
        {
            "action": "object.recolor",
            "params": {
                "object": "la moto",
                "color": "blue",
                "instance": {"strategy": "gauche"},
            },
        },
        ctx,
        {},
    )

    assert result["type"] == "ok"
    assert captured["object_label"] == "motorcycle"
    assert captured["instance_sel"] == {"strategy": "left"}
    assert captured["vision_overrides"] == {"motorcycle_body_focus": True}
    assert captured["refine"] == ("raw-motorcycle-mask", "motorcycle", resolved_bbox)
    assert [a["action"] for a in result["actions"]] == [
        "select_mask_png",
        "apply_colorize_on_selection",
        "clear_selection",
    ]
    assert result["actions"][0]["params"]["png_b64"] == "refined-motorcycle-mask"
    assert result["actions"][1]["params"]["hue"] == 240.0
    assert result["actions"][1]["params"]["recolor_mode"] == "overlay"
    assert result["actions"][1]["params"]["blend_mode"] == "color"
    assert result["actions"][1]["params"]["opacity"] == 78.0


def test_build_recolor_params_uses_overlay_defaults_for_clothes():
    executor = GimpExecutor()

    params = executor._build_recolor_params("jacket", "red")

    assert params["recolor_mode"] == "overlay"
    assert params["blend_mode"] == "overlay"
    assert params["opacity"] == 66.0
    assert params["target_hex"] == "#FF0000"
    assert params["target_rgb"] == [255, 0, 0]


def test_build_recolor_params_keeps_hsl_for_generic_non_reflective_object():
    executor = GimpExecutor()

    params = executor._build_recolor_params("person", "blue")

    assert params["recolor_mode"] == "hsl"
    assert params["blend_mode"] == "overlay"
    assert params["opacity"] == 72.0
    assert params["saturation"] >= 80.0
    assert params["lightness"] >= 18.0

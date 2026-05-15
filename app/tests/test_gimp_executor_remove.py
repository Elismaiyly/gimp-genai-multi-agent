import numpy as np

from app.executor.gimp_executor import ExecContext, GimpExecutor


def _ctx() -> ExecContext:
    return ExecContext(
        image_path="/tmp/test.png",
        image_b64="ZmFrZQ==",
        image_width=300,
        image_height=200,
        vision_agent={"serviceUrl": "http://vision.test/a2a/invoke"},
    )


def test_compile_object_remove_emits_select_inpaint_clear(monkeypatch):
    executor = GimpExecutor()
    ctx = _ctx()
    captured = {}

    def fake_resolve(object_label, _ctx, _dialog_state, instance_sel):
        captured["object_label"] = object_label
        captured["instance_sel"] = instance_sel
        return {"x": 10, "y": 20, "width": 30, "height": 40}, "raw-mask"

    def fake_refine(mask_png_b64, object_label):
        captured["refine"] = (mask_png_b64, object_label)
        return "refined-mask"

    monkeypatch.setattr(executor, "_resolve_instance_mask", fake_resolve)
    monkeypatch.setattr(executor, "_refine_mask_png_b64_for_inpaint", fake_refine)

    result = executor._compile_step(
        {
            "action": "object.remove",
            "params": {
                "object": "l'casque",
                "instance": {"strategy": "droite"},
                "inpaint_params": {"model": "lama", "radius": 12},
            },
        },
        ctx,
        {},
    )

    assert result["type"] == "ok"
    assert captured["object_label"] == "helmet"
    assert captured["instance_sel"] == {"strategy": "right"}
    assert captured["refine"] == ("raw-mask", "helmet")
    assert [a["action"] for a in result["actions"]] == [
        "select_mask_png",
        "smart_inpaint",
        "clear_selection",
    ]
    assert result["actions"][0]["params"]["png_b64"] == "refined-mask"
    assert result["actions"][1]["params"] == {"model": "lama", "radius": 12}


def test_compile_object_remove_skips_refine_when_disabled(monkeypatch):
    executor = GimpExecutor()

    monkeypatch.setattr(
        executor,
        "_resolve_instance_mask",
        lambda *args, **kwargs: ({"x": 0, "y": 0, "width": 10, "height": 10}, "raw-mask"),
    )

    def fail_refine(*args, **kwargs):
        raise AssertionError("refine should not be called when refine_mask is false")

    monkeypatch.setattr(executor, "_refine_mask_png_b64_for_inpaint", fail_refine)

    result = executor._compile_step(
        {
            "action": "object.remove",
            "params": {
                "object": "personne",
                "refine_mask": False,
            },
        },
        _ctx(),
        {},
    )

    assert result["type"] == "ok"
    assert result["actions"][0]["params"]["png_b64"] == "raw-mask"
    assert result["actions"][1]["action"] == "smart_inpaint"
    assert result["actions"][2]["action"] == "clear_selection"


def test_compile_object_remove_propagates_reliable_inpaint_mode(monkeypatch):
    executor = GimpExecutor()

    monkeypatch.setattr(
        executor,
        "_resolve_instance_mask",
        lambda *args, **kwargs: ({"x": 0, "y": 0, "width": 10, "height": 10}, "raw-mask"),
    )

    result = executor._compile_step(
        {
            "action": "object.remove",
            "params": {
                "object": "motorcycle",
                "inpaint_mode": "fast",
                "refine_mask": False,
                "inpaint_params": {
                    "opencv_radius": 7,
                },
            },
        },
        _ctx(),
        {},
    )

    assert result["type"] == "ok"
    assert result["actions"][1]["action"] == "smart_inpaint"
    assert result["actions"][1]["params"]["inpaint_mode"] == "reliable"
    assert result["actions"][1]["params"]["mode"] == "reliable"
    assert result["actions"][1]["params"]["opencv_radius"] == 7


def test_refine_inpaint_mask_keeps_large_valid_person_mask():
    executor = GimpExecutor()
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:90, 20:80] = 1

    refined, feather_radius, dilate_px = executor._refine_binary_mask_for_inpaint_object(mask, "person")

    assert refined.sum() > 0
    assert refined[50, 50] == 1
    assert feather_radius >= 5.0
    assert dilate_px == 1

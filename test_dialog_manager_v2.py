import json
from app.dialog.manager import DialogManager

dm = DialogManager()

def step(u, ctx=None):
    out = dm.handle(u, context=ctx or {})
    print("USER:", u)
    print("DM OUT:", json.dumps(out, indent=2, ensure_ascii=False))
    if getattr(dm.state, "last_planner_warnings", None):
        if dm.state.last_planner_warnings:
            print("WARNINGS:", dm.state.last_planner_warnings)
    print("-" * 60)
    return out

# 1) Manque couleur (devrait ASK color)
o1 = step("change la couleur du casque")
if o1["type"] == "ask":
    o2 = step("noir")  # devrait retourner PLAN

# 2) Manque objet (devrait ASK object puis ASK color)
o3 = step("change la couleur")
if o3["type"] == "ask":
    o4 = step("casque")
    if o4["type"] == "ask":
        o5 = step("noir")

# 3) Nonsense (devrait ASK reformulation)
step("kjzegfzeb")

# 4) Test "side" (on simule plusieurs instances)
#    Le DM ne voit pas la vision, mais si on lui donne context={"instances":[...]} il doit ASK side.
fake_instances = [{"bbox": {"x": 10, "width": 50}}, {"bbox": {"x": 300, "width": 50}}]
step("supprime", ctx={"instances": fake_instances})

# répondre au side
step("gauche")

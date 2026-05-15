const commandInput = document.getElementById("commandInput");
const jsonOutput = document.getElementById("jsonOutput");
const generateButton = document.getElementById("generateButton");
const beforeInput = document.getElementById("beforeInput");
const afterInput = document.getElementById("afterInput");
const beforePreview = document.getElementById("beforePreview");
const afterPreview = document.getElementById("afterPreview");

function inferPlan(command) {
  const text = command.trim();
  const lower = text.toLowerCase();
  const plan = { mode: "plan", plan: { actions: [] } };

  if (lower.includes("blur")) {
    plan.plan.actions.push({
      action: "gimp.filter.gaussian_blur",
      params: { radius: 5.0 },
    });
  }

  const colorMap = ["red", "blue", "green", "yellow", "black", "white", "orange", "purple", "pink", "brown"];
  const objectMap = ["jacket", "helmet", "shirt", "pants", "motorcycle", "person", "background"];
  const foundColor = colorMap.find((color) => lower.includes(color));
  const foundObject = objectMap.find((object) => lower.includes(object));

  if (lower.includes("remove") && foundObject) {
    plan.plan.actions.unshift({
      action: "object.remove",
      params: { object: foundObject },
    });
  } else if ((lower.includes("change") || lower.includes("make") || lower.includes("turn")) && foundObject && foundColor) {
    plan.plan.actions.unshift({
      action: "object.recolor",
      params: { object: foundObject, color: foundColor },
    });
  }

  if (!plan.plan.actions.length) {
    plan.mode = "ask";
    plan.text = "Need more precision about the target object or requested effect.";
    delete plan.plan;
  }

  return plan;
}

function renderPlan() {
  const plan = inferPlan(commandInput.value);
  jsonOutput.textContent = JSON.stringify(plan, null, 2);
}

function loadPreview(input, image) {
  const [file] = input.files || [];
  if (!file) return;
  const url = URL.createObjectURL(file);
  image.src = url;
}

generateButton.addEventListener("click", renderPlan);
beforeInput.addEventListener("change", () => loadPreview(beforeInput, beforePreview));
afterInput.addEventListener("change", () => loadPreview(afterInput, afterPreview));

renderPlan();

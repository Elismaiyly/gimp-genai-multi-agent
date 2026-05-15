# app/student_model/student_agent.py
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel


class StudentGimpAgent:
    def __init__(
        self,
        base_model: str = "Qwen/Qwen2.5-1.5B-Instruct",
        lora_path: Optional[str] = None,
    ):
        if lora_path is None:
            lora_path = str(Path(__file__).resolve().parent / "qwen_gimp_student_v6")

        self.system_prompt = (
            'You are a structured GIMP editing agent. '
            'You must answer with valid JSON only. '
            'The JSON schema is: '
            '{"mode":"chat|ask|plan","text":"...","slot":"...","plan":{"actions":[{"action":"...","params":{}}]}}'
        )

        print("🧠 Loading student tokenizer...")
        self.tokenizer = AutoTokenizer.from_pretrained(
            base_model,
            trust_remote_code=True
        )

        print("🧠 Loading student base model...")
        self.model = AutoModelForCausalLM.from_pretrained(
            base_model,
            trust_remote_code=True,
            dtype="auto"
        )

        print("🧠 Loading student LoRA adapter...")
        self.model = PeftModel.from_pretrained(self.model, lora_path)
        self.model.eval()

    def _extract_json(self, text: str) -> Dict[str, Any]:
        text = text.strip()

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {
                "mode": "chat",
                "text": "Je n'ai pas pu générer un JSON valide."
            }

        candidate = m.group(0)

        try:
            return json.loads(candidate)
        except Exception:
            return {
                "mode": "chat",
                "text": "Je n'ai pas pu parser la sortie JSON."
            }

    def handle(self, user_text: str, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_text},
        ]

        if hasattr(self.tokenizer, "apply_chat_template"):
            text = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
        else:
            text = f"system: {self.system_prompt}\nuser: {user_text}\nassistant:"

        inputs = self.tokenizer(text, return_tensors="pt")

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=160,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
            )

        decoded = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

        if "assistant" in decoded:
            decoded = decoded.split("assistant", 1)[-1].strip()

        parsed = self._extract_json(decoded)
        return parsed

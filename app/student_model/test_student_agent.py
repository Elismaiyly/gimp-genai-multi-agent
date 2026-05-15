# app/student_model/test_student_agent.py
from app.student_model.student_agent import StudentGimpAgent

agent = StudentGimpAgent()

while True:
    txt = input(">>> ").strip()
    if txt.lower() in {"q", "quit", "exit"}:
        break

    out = agent.handle(txt)
    print(out)
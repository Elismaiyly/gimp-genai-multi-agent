from app.agents.messages import AgentMessage


def test_agent_message_to_dict_and_from_dict_round_trip():
    message = AgentMessage(
        sender="StudentGimpAgent",
        receiver="TranslatorAgent",
        message_type="plan_v3",
        payload={"actions": [{"action": "object.remove"}]},
        metadata={"status": "success", "trace_id": "abc123"},
    )

    as_dict = message.to_dict()
    restored = AgentMessage.from_dict(as_dict)

    assert as_dict == {
        "sender": "StudentGimpAgent",
        "receiver": "TranslatorAgent",
        "message_type": "plan_v3",
        "payload": {"actions": [{"action": "object.remove"}]},
        "metadata": {"status": "success", "trace_id": "abc123"},
    }
    assert restored == message
    assert "AgentMessage(" in repr(restored)

import runpy
import sys
import types

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agent
import db


class _FakeAgent:
    def __init__(self, messages):
        self.messages = messages

    def invoke(self, state):
        return {"messages": state["messages"] + self.messages}


def test_run_agent_extracts_answer_tool_and_sources(monkeypatch):
    tool_output = "[hr_leave_policy]: 24 days. See [note] inline.\n\n[hr_wfh_policy]: WFH 2 days.\n\n[hr_leave_policy]: more"
    fake = _FakeAgent([
        AIMessage(content="", tool_calls=[{"name": "rag_search", "args": {"query": "leave"}, "id": "c1"}]),
        ToolMessage(content=tool_output, name="rag_search", tool_call_id="c1"),
        AIMessage(content=[{"type": "text", "text": "You get 24 days."}]),
    ])
    monkeypatch.setattr(agent, "get_agent", lambda: fake)
    result = agent.run_agent("leave?", [HumanMessage(content="earlier")])
    assert result["answer"] == "You get 24 days."
    assert result["action_taken"] == "rag_search"
    assert result["sources"] == ["hr_leave_policy", "hr_wfh_policy"]  # deduped, ignores inline "[note]"


def test_seed_runs_end_to_end_without_wiping_qdrant(fake_db, monkeypatch):
    captured = {}

    class FakeStore:
        @staticmethod
        def from_documents(docs, embeddings, **kwargs):
            captured["docs"], captured["kwargs"] = docs, kwargs

    monkeypatch.setitem(sys.modules, "langchain_qdrant", types.SimpleNamespace(QdrantVectorStore=FakeStore))
    import embeddings
    monkeypatch.setattr(embeddings, "FastEmbedEmbeddings", lambda **kw: object())

    runpy.run_path(str(__import__("pathlib").Path(db.__file__).with_name("seed.py")), run_name="__main__")

    assert db.rows_to_dicts(db.execute("SELECT COUNT(*) AS n FROM employees"))[0]["n"] == 20
    assert db.rows_to_dicts(db.execute("SELECT COUNT(*) AS n FROM reports"))[0]["n"] == 6
    kwargs = captured["kwargs"]
    assert not kwargs.get("force_recreate")
    assert len(kwargs["ids"]) == len(captured["docs"]) == len(set(kwargs["ids"]))

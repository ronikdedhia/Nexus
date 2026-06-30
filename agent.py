import os
from langchain.agents import create_agent
from langchain_groq import ChatGroq
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage

from tools import create_ticket, get_employee, generate_report
from rag import rag_search

SYSTEM_PROMPT = """You are Nexus, an enterprise AI assistant. You help employees with:
- HR queries: leave policy, reimbursements, benefits, employee info
- IT support: raising tickets, common troubleshooting
- Business reports: sales, HR, IT summaries

Rules:
- Use the available tools to fetch real data or take actions — do not guess.
- If a request is ambiguous (e.g. "create a ticket" with no details), ask ONE clarifying question.
- If no tool is relevant, answer from your knowledge and be honest about uncertainty.
- Be concise and professional."""

_agent = None


def get_agent():
    global _agent
    if _agent is not None:
        return _agent
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0,
        api_key=os.getenv("GROQ_API_KEY"),
    )
    _agent = create_agent(
        llm,
        tools=[create_ticket, get_employee, generate_report, rag_search],
        system_prompt=SYSTEM_PROMPT,
    )
    return _agent


def run_agent(question: str, chat_history: list[BaseMessage]) -> dict:
    agent = get_agent()
    messages = chat_history + [HumanMessage(content=question)]
    result = agent.invoke({"messages": messages})

    all_messages: list[BaseMessage] = result["messages"]

    # last AIMessage with non-empty content is the final answer
    final_answer = ""
    for msg in reversed(all_messages):
        if isinstance(msg, AIMessage) and msg.content:
            final_answer = msg.content
            break

    # find the last tool call name + result
    action_taken = None
    action_result = None
    for msg in reversed(all_messages):
        if isinstance(msg, ToolMessage):
            action_taken = getattr(msg, "name", None)
            action_result = msg.content
            break

    return {
        "answer": final_answer,
        "action_taken": action_taken,
        "action_result": action_result,
    }

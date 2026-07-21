import os
import re
from langchain.agents import create_agent
from langchain_groq import ChatGroq
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage

from tools import (
    create_ticket,
    get_employee,
    generate_report,
    get_stats,
    list_open_tickets,
    get_org_chart,
    apply_leave,
    list_departments,
    summarize_meeting,
    generate_marketing_content,
    get_digest,
)
from rag import rag_search
from metrics import record_agent_call, record_tool_call

SYSTEM_PROMPT = """You are Nexus, an enterprise AI assistant. You help employees with:
- HR queries: leave policy, reimbursements, benefits, employee info, applying leave
- IT support: raising tickets, listing open tickets, common troubleshooting
- Business reports and company-wide stats
- Org structure: who reports to whom, list of departments
- Personal daily digests (leave balance, pending requests, company ticket load)
- Summarizing meeting transcripts into decisions and action items
- Drafting on-brand marketing content (social posts, ad copy, campaign blurbs)

Rules:
- Use the available tools to fetch real data or take actions — do not guess.
- If a request is ambiguous (e.g. "create a ticket" with no details), ask ONE clarifying question.
- create_ticket checks the knowledge base first and may return a suggested self-serve answer instead
  of opening a ticket — surface that answer to the user, and only call create_ticket with
  force_create=True if they say it didn't help.
- Before calling apply_leave, confirm the employee and number of days if not explicitly clear.
  Requests over a few days become pending and need manager approval — say so.
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
        tools=[
            create_ticket,
            get_employee,
            generate_report,
            rag_search,
            get_stats,
            list_open_tickets,
            get_org_chart,
            apply_leave,
            list_departments,
            summarize_meeting,
            generate_marketing_content,
            get_digest,
        ],
        system_prompt=SYSTEM_PROMPT,
    )
    return _agent


def run_agent(question: str, chat_history: list[BaseMessage]) -> dict:
    record_agent_call()
    agent = get_agent()
    messages = chat_history + [HumanMessage(content=question)]
    result = agent.invoke({"messages": messages})

    all_messages: list[BaseMessage] = result["messages"]
    for msg in all_messages:
        if isinstance(msg, ToolMessage):
            record_tool_call(getattr(msg, "name", "unknown"))

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

    sources = None
    if action_taken == "rag_search" and isinstance(action_result, str):
        sources = re.findall(r"\[([^\]]+)\]", action_result) or None

    return {
        "answer": final_answer,
        "action_taken": action_taken,
        "action_result": action_result,
        "sources": sources,
    }

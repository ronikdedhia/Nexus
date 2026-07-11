import threading
from collections import defaultdict

_lock = threading.Lock()
_requests_total = 0
_requests_by_path = defaultdict(int)
_agent_calls_total = 0
_tool_calls = defaultdict(int)


def record_request(path: str):
    global _requests_total
    with _lock:
        _requests_total += 1
        _requests_by_path[path] += 1


def record_agent_call():
    global _agent_calls_total
    with _lock:
        _agent_calls_total += 1


def record_tool_call(tool_name: str):
    with _lock:
        _tool_calls[tool_name] += 1


def render_prometheus() -> str:
    with _lock:
        lines = [
            "# HELP nexus_requests_total Total HTTP requests served",
            "# TYPE nexus_requests_total counter",
            f"nexus_requests_total {_requests_total}",
            "# HELP nexus_requests_by_path_total HTTP requests served per path",
            "# TYPE nexus_requests_by_path_total counter",
        ]
        for path, count in _requests_by_path.items():
            lines.append(f'nexus_requests_by_path_total{{path="{path}"}} {count}')
        lines += [
            "# HELP nexus_agent_calls_total Total /ask agent invocations",
            "# TYPE nexus_agent_calls_total counter",
            f"nexus_agent_calls_total {_agent_calls_total}",
            "# HELP nexus_tool_calls_total Agent tool invocations by tool name",
            "# TYPE nexus_tool_calls_total counter",
        ]
        for tool, count in _tool_calls.items():
            lines.append(f'nexus_tool_calls_total{{tool="{tool}"}} {count}')
    return "\n".join(lines) + "\n"

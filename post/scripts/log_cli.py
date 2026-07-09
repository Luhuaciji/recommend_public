from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class LogClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, method=method)
        if method in {"POST", "DELETE"}:
            request.add_header("Content-Length", "0")
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"request failed: {exc}") from exc
        if not body:
            return {}
        return json.loads(body)

    def list_conversations(self, limit: int = 50) -> List[Dict[str, Any]]:
        result = self.request("GET", "/admin/logs/conversations", {"limit": limit})
        return result.get("items", [])

    def search_conversations(self, start: str, end: str, limit: int = 50) -> List[Dict[str, Any]]:
        result = self.request(
            "GET",
            "/admin/logs/conversations/search",
            {"start": start, "end": end, "limit": limit},
        )
        return result.get("items", [])

    def conversation(self, conversation_id: str) -> Dict[str, Any]:
        return self.request(
            "GET",
            f"/admin/logs/conversations/{quote(conversation_id, safe='')}",
        )

    def messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        result = self.request(
            "GET",
            f"/admin/logs/conversations/{quote(conversation_id, safe='')}/messages",
        )
        return result.get("messages", [])

    def events(self, conversation_id: str) -> List[Dict[str, Any]]:
        result = self.request(
            "GET",
            f"/admin/logs/conversations/{quote(conversation_id, safe='')}/events",
        )
        return result.get("events", [])

    def task(self, task_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/admin/logs/tasks/{quote(task_id, safe='')}")

    def delete_conversation(self, conversation_id: str) -> Dict[str, Any]:
        return self.request(
            "DELETE",
            f"/admin/logs/conversations/{quote(conversation_id, safe='')}",
        )

    def delete_task(self, task_id: str) -> Dict[str, Any]:
        return self.request("DELETE", f"/admin/logs/tasks/{quote(task_id, safe='')}")

    def cleanup(self, days: int = 10) -> Dict[str, Any]:
        return self.request("POST", "/admin/logs/cleanup", {"days": days})


class InteractiveShell:
    def __init__(self, client: LogClient):
        self.client = client
        self.last_items: List[Dict[str, Any]] = []
        self.current_conversation_id: Optional[str] = None
        self.last_limit = 50

    def run(self) -> None:
        print(f"Connected: {self.client.base_url}")
        print("Type 'help' for commands.")
        while True:
            try:
                raw = input("logs> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not raw:
                continue
            try:
                if not self.execute(raw):
                    return
            except Exception as exc:
                print(f"error: {exc}")

    def execute(self, raw: str) -> bool:
        parts = shlex.split(raw)
        if not parts:
            return True
        command = parts[0].lower()
        args = parts[1:]

        if command in {"quit", "exit", "q"}:
            return False
        if command in {"help", "h", "?"}:
            self.print_help()
        elif command == "list":
            limit = self._int_arg(args, default=self.last_limit)
            self.do_list(limit)
        elif command == "refresh":
            self.do_list(self.last_limit)
        elif command == "search":
            start, end, limit = self.parse_search_args(args)
            self.do_search(start, end, limit)
        elif command in {"open", "show"}:
            conversation_id = self.resolve_conversation_id(args[0] if args else None)
            self.do_show(conversation_id)
        elif command == "export":
            conversation_id, output_format = self.parse_export_args(args)
            self.do_export(conversation_id, output_format)
        elif command == "export-current":
            output_format = args[0].lower() if args else "json"
            conversation_id = self.resolve_conversation_id(None)
            self.do_export(conversation_id, output_format)
        elif command == "messages":
            conversation_id = self.resolve_conversation_id(args[0] if args else None)
            self.print_messages(self.client.messages(conversation_id))
        elif command == "events":
            conversation_id = self.resolve_conversation_id(args[0] if args else None)
            self.print_events(self.client.events(conversation_id))
        elif command == "llm":
            conversation_id = self.resolve_conversation_id(args[0] if args else None)
            self.print_llm_summaries(self.client.events(conversation_id))
        elif command == "task":
            if not args:
                raise ValueError("usage: task <task_id>")
            self.do_task(args[0])
        elif command == "delete":
            conversation_id = self.resolve_conversation_id(args[0] if args else None)
            self.do_delete(conversation_id)
        elif command == "delete-current":
            conversation_id = self.resolve_conversation_id(None)
            self.do_delete(conversation_id)
        elif command == "delete-task":
            if not args:
                raise ValueError("usage: delete-task <task_id>")
            self.do_delete_task(args[0])
        elif command == "cleanup":
            days = self._int_arg(args, default=10)
            self.do_cleanup(days)
        elif command == "current":
            print(self.current_conversation_id or "no current conversation")
        else:
            print(f"unknown command: {command}")
            print("Type 'help' for commands.")
        return True

    def do_list(self, limit: int) -> None:
        self.last_limit = limit
        self.last_items = self.client.list_conversations(limit=limit)
        self.print_conversation_list(self.last_items)

    def do_search(self, start: str, end: str, limit: int) -> None:
        self.last_limit = limit
        self.last_items = self.client.search_conversations(start=start, end=end, limit=limit)
        print(f"search range: {start} -> {end}")
        self.print_conversation_list(self.last_items)

    def print_conversation_list(self, items: List[Dict[str, Any]]) -> None:
        if not items:
            print("no conversations")
            return
        for index, item in enumerate(items, start=1):
            matched = ""
            if "matched_message_count" in item or "matched_event_count" in item:
                matched = (
                    f" | matched_messages={item.get('matched_message_count', 0)}"
                    f" | matched_events={item.get('matched_event_count', 0)}"
                )
            print(
                f"[{index}] {item.get('created_at', '')} | "
                f"updated {item.get('updated_at', '')} | "
                f"conv={item.get('conversation_id', '')} | "
                f"user={item.get('user_id', '')} | "
                f"stage={item.get('last_stage', '')} | "
                f"messages={item.get('message_count', 0)}"
                f"{matched}"
            )

    def do_show(self, conversation_id: str) -> None:
        detail = self.client.conversation(conversation_id)
        self.current_conversation_id = conversation_id
        conversation = detail.get("conversation", {})
        print(json.dumps(conversation, ensure_ascii=False, indent=2))
        print("\n--- messages ---")
        self.print_messages(detail.get("messages", []))
        print("\n--- events ---")
        self.print_events(detail.get("events", []), compact=True)

    def do_task(self, task_id: str) -> None:
        logs = self.client.task(task_id)
        print(f"task_id={task_id}")
        print("\n--- messages ---")
        self.print_messages(logs.get("messages", []))
        print("\n--- events ---")
        self.print_events(logs.get("events", []))

    def do_delete(self, conversation_id: str) -> None:
        confirm = input(f"Delete conversation_id={conversation_id}? Type yes to continue: ")
        if confirm != "yes":
            print("cancelled")
            return
        result = self.client.delete_conversation(conversation_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if self.current_conversation_id == conversation_id:
            self.current_conversation_id = None
        self.last_items = [
            item
            for item in self.last_items
            if item.get("conversation_id") != conversation_id
        ]

    def do_delete_task(self, task_id: str) -> None:
        confirm = input(f"Delete task_id={task_id}? Type yes to continue: ")
        if confirm != "yes":
            print("cancelled")
            return
        result = self.client.delete_task(task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def do_cleanup(self, days: int) -> None:
        confirm = input(f"Delete conversations older than {days} days? Type yes to continue: ")
        if confirm != "yes":
            print("cancelled")
            return
        result = self.client.cleanup(days=days)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def do_export(self, conversation_id: str, output_format: str = "json") -> None:
        output_format = output_format.lower()
        if output_format not in {"json", "md", "markdown"}:
            raise ValueError("export format must be json or md")
        detail = self.client.conversation(conversation_id)
        self.current_conversation_id = conversation_id
        export_dir = Path("exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        ext = "md" if output_format in {"md", "markdown"} else "json"
        filename = (
            f"conversation_{self._safe_filename(conversation_id)}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
        )
        path = export_dir / filename
        if ext == "json":
            payload = {
                "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": self.client.base_url,
                **detail,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path.write_text(
                self.render_markdown_export(detail, source=self.client.base_url),
                encoding="utf-8",
            )
        print(f"saved: {path}")

    def print_messages(self, messages: List[Dict[str, Any]]) -> None:
        if not messages:
            print("no messages")
            return
        for item in messages:
            print(
                f"\n[{item.get('id')}] {item.get('created_at')} "
                f"{item.get('role')} task={item.get('task_id')}"
            )
            content = item.get("content") or ""
            if content:
                print(content)
            data_blocks = item.get("data_blocks")
            if data_blocks:
                product_cards = self._extract_product_cards(data_blocks)
                if product_cards:
                    print("product_cards:")
                    for index, card in enumerate(product_cards, start=1):
                        print(
                            f"  {index}. {card.get('productName', '')} | "
                            f"id={card.get('productId', '')} | "
                            f"price={card.get('payPrice', '')} | "
                            f"showStrategy={card.get('showStrategy')}"
                        )
                print("data_blocks:")
                print(json.dumps(data_blocks, ensure_ascii=False, indent=2))

    def print_events(self, events: List[Dict[str, Any]], compact: bool = False) -> None:
        if not events:
            print("no events")
            return
        for item in events:
            print(
                f"\n[{item.get('id')}] {item.get('created_at')} "
                f"{item.get('event_type')} task={item.get('task_id')}"
            )
            payload = item.get("payload") or {}
            if compact:
                print(self._compact_event_payload(item.get("event_type", ""), payload))
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))

    def print_llm_summaries(self, events: List[Dict[str, Any]]) -> None:
        llm_events = [
            item for item in events
            if item.get("event_type") == "llm_call_summary"
        ]
        if not llm_events:
            print("no llm call summaries")
            return

        for event in llm_events:
            payload = event.get("payload") or {}
            summary = payload.get("summary") or {}
            print(
                f"\n{event.get('created_at')} task={event.get('task_id')} | "
                f"calls={summary.get('total_calls', 0)} "
                f"success={summary.get('success_calls', 0)} "
                f"failed={summary.get('failed_calls', 0)} | "
                f"wall={summary.get('wall_span_ms', 0)}ms "
                f"sum={summary.get('sum_duration_ms', 0)}ms | "
                f"tokens={summary.get('total_tokens')}"
            )
            timeline = payload.get("timeline") or []
            for call in timeline:
                group = call.get("parallel_group") or "serial"
                tokens = call.get("total_tokens")
                token_text = f" tokens={tokens}" if tokens is not None else ""
                print(
                    f"  #{call.get('sequence')} "
                    f"done#{call.get('completion_sequence')} "
                    f"+{call.get('start_offset_ms')}ms "
                    f"{call.get('duration_ms')}ms "
                    f"[{call.get('status')}] "
                    f"{call.get('call_name')} "
                    f"group={group}{token_text}"
                )

    def render_markdown_export(self, detail: Dict[str, Any], source: str) -> str:
        conversation = detail.get("conversation", {}) or {}
        messages = detail.get("messages", []) or []
        events = detail.get("events", []) or []
        lines = [
            "# Conversation Log Export",
            "",
            f"- Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Source: {source}",
            f"- Conversation ID: {conversation.get('conversation_id', '')}",
            f"- User ID: {conversation.get('user_id', '')}",
            f"- Created at: {conversation.get('created_at', '')}",
            f"- Updated at: {conversation.get('updated_at', '')}",
            f"- Last task ID: {conversation.get('last_task_id', '')}",
            f"- Last stage: {conversation.get('last_stage', '')}",
            f"- Message count: {conversation.get('message_count', 0)}",
            "",
            "## Messages",
            "",
        ]

        if not messages:
            lines.append("No messages.")
        for item in messages:
            lines.extend(
                [
                    f"### {item.get('role', '')} | {item.get('created_at', '')}",
                    "",
                    f"- Message ID: {item.get('id')}",
                    f"- Task ID: {item.get('task_id', '')}",
                    "",
                ]
            )
            content = item.get("content") or ""
            if content:
                lines.extend([content, ""])
            data_blocks = item.get("data_blocks")
            if data_blocks:
                product_cards = self._extract_product_cards(data_blocks)
                if product_cards:
                    lines.extend(["Product cards:", ""])
                    for index, card in enumerate(product_cards, start=1):
                        lines.append(
                            f"{index}. {card.get('productName', '')} | "
                            f"id={card.get('productId', '')} | "
                            f"price={card.get('payPrice', '')} | "
                            f"showStrategy={card.get('showStrategy')}"
                        )
                    lines.append("")
                lines.extend(
                    [
                        "Data blocks:",
                        "",
                        "```json",
                        json.dumps(data_blocks, ensure_ascii=False, indent=2),
                        "```",
                        "",
                    ]
                )

        lines.extend(["## Events", ""])
        if not events:
            lines.append("No events.")
        for item in events:
            lines.extend(
                [
                    f"### {item.get('event_type', '')} | {item.get('created_at', '')}",
                    "",
                    f"- Event ID: {item.get('id')}",
                    f"- Task ID: {item.get('task_id', '')}",
                    "",
                    "```json",
                    json.dumps(item.get("payload") or {}, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def resolve_conversation_id(self, value: Optional[str]) -> str:
        if value is None:
            if not self.current_conversation_id:
                raise ValueError("no current conversation; use list then open <index|conversation_id>")
            return self.current_conversation_id
        if value.isdigit():
            index = int(value)
            if index < 1 or index > len(self.last_items):
                raise ValueError(f"list index out of range: {index}")
            return str(self.last_items[index - 1].get("conversation_id", ""))
        return value

    def parse_export_args(self, args: List[str]) -> tuple[str, str]:
        if not args:
            return self.resolve_conversation_id(None), "json"
        if args[0].lower() in {"json", "md", "markdown"}:
            return self.resolve_conversation_id(None), args[0].lower()
        conversation_id = self.resolve_conversation_id(args[0])
        output_format = args[1].lower() if len(args) > 1 else "json"
        return conversation_id, output_format

    def parse_search_args(self, args: List[str]) -> tuple[str, str, int]:
        if not args:
            raise ValueError(
                'usage: search "YYYY-MM-DD HH:MM:SS" "YYYY-MM-DD HH:MM:SS" [limit]'
            )
        now = datetime.now()
        first = args[0].lower()
        limit = self.last_limit

        if first == "today":
            start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return self._fmt_dt(start_dt), self._fmt_dt(now), self._optional_limit(args[1:], limit)
        if first == "yesterday":
            yesterday = now - timedelta(days=1)
            start_dt = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
            return self._fmt_dt(start_dt), self._fmt_dt(end_dt), self._optional_limit(args[1:], limit)
        if first == "last":
            if len(args) < 2:
                raise ValueError("usage: search last <30m|2h|7d> [limit]")
            delta = self._parse_duration(args[1])
            return self._fmt_dt(now - delta), self._fmt_dt(now), self._optional_limit(args[2:], limit)

        if len(args) >= 4 and self._looks_like_date(args[0]) and self._looks_like_time(args[1]):
            start = f"{args[0]} {args[1]}"
            end = f"{args[2]} {args[3]}"
            return start, end, self._optional_limit(args[4:], limit)
        if len(args) >= 2:
            return args[0], args[1], self._optional_limit(args[2:], limit)

        raise ValueError(
            'usage: search "YYYY-MM-DD HH:MM:SS" "YYYY-MM-DD HH:MM:SS" [limit]'
        )

    def print_help(self) -> None:
        print(
            """
Commands:
  list [limit]               List conversations with created_at.
  refresh                    Re-run the last list command.
  search "start" "end" [limit]
                             Search conversations by time range.
  search today [limit]        Search from today's 00:00:00 to now.
  search yesterday [limit]    Search yesterday's logs.
  search last <30m|2h|7d> [limit]
                             Search logs in the recent duration.
  open <index|conversation>   Show full conversation and compact events.
  export [index|conversation] [json|md]
                             Export a conversation to ./exports.
  export-current [json|md]    Export current conversation to ./exports.
  messages [index|conversation]
                             Show messages for current or selected conversation.
  events [index|conversation]
                             Show events for current or selected conversation.
  llm [index|conversation]   Show centralized LLM counts and timeline.
  task <task_id>              Show logs for one task.
  current                    Show current conversation_id.
  delete <index|conversation> Delete one conversation after confirmation.
  delete-current             Delete current conversation after confirmation.
  delete-task <task_id>       Delete one task's messages and events.
  cleanup [days]             Delete conversations older than days, default 10.
  help                       Show this help.
  quit                       Exit.
""".strip()
        )

    @staticmethod
    def _int_arg(args: List[str], default: int) -> int:
        if not args:
            return default
        try:
            value = int(args[0])
        except ValueError as exc:
            raise ValueError(f"expected integer, got: {args[0]}") from exc
        return max(1, value)

    @staticmethod
    def _optional_limit(args: List[str], default: int) -> int:
        if not args:
            return default
        try:
            return max(1, int(args[0]))
        except ValueError as exc:
            raise ValueError(f"expected integer limit, got: {args[0]}") from exc

    @staticmethod
    def _fmt_dt(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _looks_like_date(value: str) -> bool:
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", value))

    @staticmethod
    def _looks_like_time(value: str) -> bool:
        return bool(re.match(r"^\d{2}:\d{2}:\d{2}$", value))

    @staticmethod
    def _parse_duration(value: str) -> timedelta:
        match = re.match(r"^(\d+)([mhd])$", value.lower())
        if not match:
            raise ValueError("duration must look like 30m, 2h, or 7d")
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "m":
            return timedelta(minutes=amount)
        if unit == "h":
            return timedelta(hours=amount)
        return timedelta(days=amount)

    @staticmethod
    def _safe_filename(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
        return cleaned.strip("._") or "conversation"

    @staticmethod
    def _extract_product_cards(data_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cards: List[Dict[str, Any]] = []
        for block in data_blocks or []:
            payload = block
            content = str(block.get("content", "") or "").strip()
            if content.startswith("```json"):
                content = content[len("```json") :].strip()
                if content.endswith("```"):
                    content = content[:-3].strip()
                try:
                    payload = json.loads(content)
                except Exception:
                    payload = block
            if isinstance(payload, dict) and payload.get("type") == "pro-recommend":
                data = payload.get("data", [])
                if isinstance(data, list):
                    cards.extend([item for item in data if isinstance(item, dict)])
        return cards

    @staticmethod
    def _compact_event_payload(event_type: str, payload: Dict[str, Any]) -> str:
        if event_type == "gift_state_snapshot":
            return json.dumps(
                {
                    "stage": payload.get("stage"),
                    "filled_slots": payload.get("filled_slots"),
                    "selected_category": payload.get("selected_category"),
                    "task_boundary_decision": payload.get("task_boundary_decision"),
                    "pending_categories": payload.get("pending_categories"),
                    "final_product_cards": payload.get("final_product_cards"),
                },
                ensure_ascii=False,
                indent=2,
            )
        if event_type == "recommendation_analysis":
            return json.dumps(
                {
                    "stage": payload.get("stage"),
                    "action": payload.get("action"),
                    "task_boundary_decision": payload.get("task_boundary_decision"),
                    "product_cards": payload.get("product_cards"),
                    "downgrade_retry_triggered": payload.get("downgrade_retry_triggered"),
                    "downgrade_retry_reason": payload.get("downgrade_retry_reason"),
                },
                ensure_ascii=False,
                indent=2,
            )
        if event_type == "llm_call_summary":
            timeline = []
            for call in payload.get("timeline", []) or []:
                timeline.append(
                    {
                        "sequence": call.get("sequence"),
                        "completion_sequence": call.get("completion_sequence"),
                        "call_name": call.get("call_name"),
                        "parallel_group": call.get("parallel_group"),
                        "start_offset_ms": call.get("start_offset_ms"),
                        "duration_ms": call.get("duration_ms"),
                        "status": call.get("status"),
                        "total_tokens": call.get("total_tokens"),
                    }
                )
            return json.dumps(
                {
                    "summary": payload.get("summary", {}),
                    "timeline": timeline,
                },
                ensure_ascii=False,
                indent=2,
            )
        return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive conversation log viewer")
    parser.add_argument("--host", default="127.0.0.1", help="Remote host, default 127.0.0.1")
    parser.add_argument("--port", default=8001, type=int, help="Remote port, default 8000")
    parser.add_argument("--base-url", default="", help="Full base URL, e.g. http://host:8000")
    return parser.parse_args(argv)


def build_base_url(args: argparse.Namespace) -> str:
    if args.base_url:
        return args.base_url
    host = args.host
    if host.startswith("http://") or host.startswith("https://"):
        return f"{host.rstrip('/')}:{args.port}" if ":" not in host.split("//", 1)[1] else host
    return f"http://{host}:{args.port}"


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    shell = InteractiveShell(LogClient(build_base_url(args)))
    shell.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

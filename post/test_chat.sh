#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
fi

CDF_DEFAULT_BASE_URL="http://127.0.0.1:8000"
CDF_DEFAULT_APP_ID="cdf_26283b073aa0433a"
CDF_DEFAULT_APP_SECRET="6c89a8e9a12b833ffefe0819b0db61c35229d023371f6f75667ebadc033d0ed4"
CDF_DEFAULT_USER_ID="u100"

_cdf_python_cmd=""

trim_whitespace() {
    local value="${1-}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

strip_cr() {
    local value="${1-}"
    value="${value//$'\r'/}"
    printf '%s' "$value"
}

is_blank() {
    [[ -z "$(trim_whitespace "${1-}")" ]]
}

get_cdf_config_value() {
    local env_name="$1"
    local default_value="$2"
    local env_value="${!env_name-}"

    if [[ -z "${env_value//[[:space:]]/}" ]]; then
        printf '%s' "$default_value"
        return 0
    fi

    printf '%s' "$env_value"
}

get_python_cmd() {
    if [[ -n "$_cdf_python_cmd" ]]; then
        printf '%s' "$_cdf_python_cmd"
        return 0
    fi

    if [[ -n "${CDF_PYTHON_BIN-}" ]] && command -v "$CDF_PYTHON_BIN" >/dev/null 2>&1; then
        _cdf_python_cmd="$CDF_PYTHON_BIN"
        printf '%s' "$_cdf_python_cmd"
        return 0
    fi

    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            _cdf_python_cmd="$candidate"
            printf '%s' "$_cdf_python_cmd"
            return 0
        fi
    done

    printf 'Missing required command: python3 or python\n' >&2
    return 1
}

run_python_utf8() {
    local python_cmd
    python_cmd="$(get_python_cmd)" || return 1
    PYTHONIOENCODING=UTF-8 "$python_cmd" "$@"
}

new_cdf_timestamp() {
    run_python_utf8 - <<'PY'
import time
print(int(time.time()))
PY
}

new_cdf_nonce() {
    run_python_utf8 - <<'PY'
import uuid
print(uuid.uuid4().hex)
PY
}

new_cdf_conversation_id() {
    run_python_utf8 - <<'PY'
from datetime import datetime
print("gift" + datetime.now().strftime("%Y%m%d%H%M%S"))
PY
}

new_cdf_task_id() {
    local task_prefix="${1:-task}"
    local task_number="${2:-1}"
    printf '%s%03d\n' "$task_prefix" "$task_number"
}

print_stderr_color() {
    local color_code="$1"
    local message="$2"

    if [[ -t 2 ]]; then
        printf '\033[%sm%s\033[0m\n' "$color_code" "$message" >&2
    else
        printf '%s\n' "$message" >&2
    fi
}

print_json_pretty() {
    local payload="${1-}"

    if ! run_python_utf8 - "$payload" <<'PY'
import json
import sys

payload = sys.argv[1]
print(json.dumps(json.loads(payload), ensure_ascii=False, indent=4))
PY
    then
        printf '%s\n' "$payload"
    fi
}

convert_to_cdf_chat_histories() {
    local chat_histories_json="${1:-[]}"

    run_python_utf8 - "$chat_histories_json" <<'PY'
import json
import sys

raw = sys.argv[1]
if raw is None or raw.strip() == "":
    print("[]")
    raise SystemExit(0)

parsed = json.loads(raw)
if parsed is None:
    parsed = []
elif not isinstance(parsed, list):
    parsed = [parsed]

print(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
PY
}

build_chat_body() {
    local conversation_id="$1"
    local task_id="$2"
    local query_text="$3"
    local chat_histories_json="$4"
    local user_id="$5"

    run_python_utf8 - "$conversation_id" "$task_id" "$query_text" "$chat_histories_json" "$user_id" <<'PY'
import json
import sys

conversation_id, task_id, query_text, chat_histories_json, user_id = sys.argv[1:]
body = {
    "ConversationID": conversation_id,
    "taskId": task_id,
    "Query": json.dumps({"queryText": query_text}, ensure_ascii=False, separators=(",", ":")),
    "ChatHistories": json.loads(chat_histories_json),
    "UserID": user_id,
    "IsInterrupt": False,
}
print(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
PY
}

build_interrupt_body() {
    local conversation_id="$1"
    local task_id="$2"
    local user_id="$3"

    run_python_utf8 - "$conversation_id" "$task_id" "$user_id" <<'PY'
import json
import sys

conversation_id, task_id, user_id = sys.argv[1:]
body = {
    "ConversationID": conversation_id,
    "taskId": task_id,
    "Query": json.dumps({"queryText": ""}, ensure_ascii=False, separators=(",", ":")),
    "ChatHistories": [],
    "UserID": user_id,
    "IsInterrupt": True,
}
print(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
PY
}

new_cdf_signature() {
    local body_json="$1"
    local app_id="$2"
    local app_secret="$3"
    local nonce="$4"
    local timestamp="$5"

    run_python_utf8 - "$body_json" "$app_id" "$app_secret" "$nonce" "$timestamp" <<'PY'
import hashlib
import hmac
import json
import sys

body_json, app_id, app_secret, nonce, timestamp = sys.argv[1:]
body = json.loads(body_json)

sign_dict = {
    "appid": app_id,
    "timestamp": str(timestamp),
    "nonce": nonce,
}

for key, value in body.items():
    if value is None:
        continue
    if isinstance(value, str):
        sign_dict[key] = value
    elif isinstance(value, (dict, list)):
        sign_dict[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(value, bool):
        sign_dict[key] = "True" if value else "False"
    else:
        sign_dict[key] = str(value)

message = "&".join(f"{key}={sign_dict[key]}" for key in sorted(sign_dict.keys()))
signature = hmac.new(
    app_secret.encode("utf-8"),
    message.encode("utf-8"),
    digestmod=hashlib.sha256,
).hexdigest()

print(message)
print(signature)
PY
}

send_cdf_request() {
    local body_json="$1"
    local base_url="$2"
    local app_id="$3"
    local nonce="$4"
    local timestamp="$5"
    local signature="$6"
    local request_url="${base_url%/}/cdfai/v1/fudan/chat"

    run_python_utf8 - "$request_url" "$app_id" "$timestamp" "$nonce" "$signature" "$body_json" <<'PY'
import sys
import urllib.error
import urllib.request

request_url, app_id, timestamp, nonce, signature, body_json = sys.argv[1:]
data = body_json.encode("utf-8")
request = urllib.request.Request(
    request_url,
    data=data,
    method="POST",
    headers={
        "appid": app_id,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
        "Content-Type": "application/json; charset=utf-8",
    },
)

try:
    with urllib.request.urlopen(request) as response:
        sys.stdout.write(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    sys.stderr.write(body + ("\n" if not body.endswith("\n") else ""))
    raise SystemExit(1)
except Exception as exc:
    sys.stderr.write(f"{exc}\n")
    raise SystemExit(1)
PY
}

invoke_cdf_request() {
    local body_json="$1"
    local base_url="$2"
    local app_id="$3"
    local app_secret="$4"
    local is_interrupt="${5:-false}"

    local timestamp
    local nonce
    local sign_parts
    local sign_message
    local signature

    timestamp="$(strip_cr "$(new_cdf_timestamp)")"
    nonce="$(strip_cr "$(new_cdf_nonce)")"
    # 兼容 macOS 自带 Bash 3.2（无 mapfile）
    local sign_output sign_line
    sign_output=""
    while IFS= read -r sign_line || [[ -n "$sign_line" ]]; do
        sign_output+="${sign_line}"$'\n'
    done < <(new_cdf_signature "$body_json" "$app_id" "$app_secret" "$nonce" "$timestamp")
    sign_message="$(strip_cr "$(printf '%s' "$sign_output" | sed -n '1p')")"
    signature="$(strip_cr "$(printf '%s' "$sign_output" | sed -n '2p')")"

    if [[ "$is_interrupt" == "true" ]]; then
        print_stderr_color "36" "=== Client Interrupt Sign Message ==="
    else
        print_stderr_color "36" "=== Client Sign Message ==="
    fi
    printf '%s\n' "$sign_message" >&2

    if [[ "$is_interrupt" == "true" ]]; then
        print_stderr_color "36" "=== Client Interrupt Signature ==="
    else
        print_stderr_color "36" "=== Client Signature ==="
    fi
    printf '%s\n' "$signature" >&2

    send_cdf_request "$body_json" "$base_url" "$app_id" "$nonce" "$timestamp" "$signature"
}

invoke_cdf_chat() {
    local conversation_id=""
    local task_id=""
    local query_text=""
    local chat_histories_json="[]"
    local base_url
    local app_id
    local app_secret
    local user_id
    local normalized_chat_histories_json
    local body_json

    base_url="$(get_cdf_config_value "CDF_BASE_URL" "$CDF_DEFAULT_BASE_URL")"
    app_id="$(get_cdf_config_value "CDF_APP_ID" "$CDF_DEFAULT_APP_ID")"
    app_secret="$(get_cdf_config_value "CDF_APP_SECRET" "$CDF_DEFAULT_APP_SECRET")"
    user_id="$(get_cdf_config_value "CDF_USER_ID" "$CDF_DEFAULT_USER_ID")"

    while (( $# > 0 )); do
        case "$1" in
            -ConversationID|--conversation-id)
                conversation_id="${2-}"
                shift 2
                ;;
            -TaskId|--task-id)
                task_id="${2-}"
                shift 2
                ;;
            -QueryText|--query-text)
                query_text="${2-}"
                shift 2
                ;;
            -ChatHistoriesJson|--chat-histories-json)
                chat_histories_json="${2-}"
                shift 2
                ;;
            -BaseUrl|--base-url)
                base_url="${2-}"
                shift 2
                ;;
            -AppId|--app-id)
                app_id="${2-}"
                shift 2
                ;;
            -AppSecret|--app-secret)
                app_secret="${2-}"
                shift 2
                ;;
            -UserId|--user-id)
                user_id="${2-}"
                shift 2
                ;;
            *)
                printf 'Unknown option for invoke_cdf_chat: %s\n' "$1" >&2
                return 1
                ;;
        esac
    done

    if is_blank "$conversation_id"; then
        printf 'invoke_cdf_chat requires -ConversationID.\n' >&2
        return 1
    fi
    if is_blank "$task_id"; then
        printf 'invoke_cdf_chat requires -TaskId.\n' >&2
        return 1
    fi
    if is_blank "$query_text"; then
        printf 'invoke_cdf_chat requires -QueryText.\n' >&2
        return 1
    fi

    normalized_chat_histories_json="$(convert_to_cdf_chat_histories "$chat_histories_json")"
    body_json="$(build_chat_body "$conversation_id" "$task_id" "$query_text" "$normalized_chat_histories_json" "$user_id")"
    invoke_cdf_request "$body_json" "$base_url" "$app_id" "$app_secret" "false"
}

invoke_cdf_interrupt() {
    local conversation_id=""
    local task_id=""
    local base_url
    local app_id
    local app_secret
    local user_id
    local body_json

    base_url="$(get_cdf_config_value "CDF_BASE_URL" "$CDF_DEFAULT_BASE_URL")"
    app_id="$(get_cdf_config_value "CDF_APP_ID" "$CDF_DEFAULT_APP_ID")"
    app_secret="$(get_cdf_config_value "CDF_APP_SECRET" "$CDF_DEFAULT_APP_SECRET")"
    user_id="$(get_cdf_config_value "CDF_USER_ID" "$CDF_DEFAULT_USER_ID")"

    while (( $# > 0 )); do
        case "$1" in
            -ConversationID|--conversation-id)
                conversation_id="${2-}"
                shift 2
                ;;
            -TaskId|--task-id)
                task_id="${2-}"
                shift 2
                ;;
            -BaseUrl|--base-url)
                base_url="${2-}"
                shift 2
                ;;
            -AppId|--app-id)
                app_id="${2-}"
                shift 2
                ;;
            -AppSecret|--app-secret)
                app_secret="${2-}"
                shift 2
                ;;
            -UserId|--user-id)
                user_id="${2-}"
                shift 2
                ;;
            *)
                printf 'Unknown option for invoke_cdf_interrupt: %s\n' "$1" >&2
                return 1
                ;;
        esac
    done

    if is_blank "$conversation_id"; then
        printf 'invoke_cdf_interrupt requires -ConversationID.\n' >&2
        return 1
    fi
    if is_blank "$task_id"; then
        printf 'invoke_cdf_interrupt requires -TaskId.\n' >&2
        return 1
    fi

    body_json="$(build_interrupt_body "$conversation_id" "$task_id" "$user_id")"
    invoke_cdf_request "$body_json" "$base_url" "$app_id" "$app_secret" "true"
}

write_cdf_chat_response() {
    local response_json="${1-}"

    if is_blank "$response_json"; then
        response_json="$(cat)"
    fi

    run_python_utf8 - "$response_json" <<'PY'
import json
import sys

response_json = sys.argv[1]
response = json.loads(response_json)

task_id = str(response.get("taskId", "") or "").strip()
if task_id:
    print(f"taskId: {task_id}")

items = response.get("data")
if items is None:
    items = response.get("data_blocks", [])

if not isinstance(items, list):
    items = [items]

rendered = False
for item in items:
    if item is None:
        continue
    item_type = str(item.get("type", "") or "").strip()
    content = item.get("content")
    content_text = "" if content is None else str(content)

    if item_type == "text" and content_text.strip():
        print(f"助手> {content_text}")
        rendered = True
        continue

    if item_type:
        print(f"助手[{item_type}]>")

    if content is not None and content_text.strip():
        print(content_text)
        rendered = True
    else:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        rendered = True

if not rendered:
    print(json.dumps(response, ensure_ascii=False, indent=2))
PY
}

show_cdf_interactive_help() {
    printf '%s\n' '交互命令：'
    printf '%s\n' '  /help        查看帮助'
    printf '%s\n' '  /interrupt   发送中断请求'
    printf '%s\n' '  /new         新开一个会话 ID'
    printf '%s\n' '  /new conv9   切换到指定会话 ID'
    printf '%s\n' '  /exit        退出交互模式'
}

start_cdf_chat_interactive() {
    local conversation_id=""
    local task_prefix="task"
    local start_task_number=1
    local base_url
    local app_id
    local app_secret
    local user_id
    local task_number

    base_url="$(get_cdf_config_value "CDF_BASE_URL" "$CDF_DEFAULT_BASE_URL")"
    app_id="$(get_cdf_config_value "CDF_APP_ID" "$CDF_DEFAULT_APP_ID")"
    app_secret="$(get_cdf_config_value "CDF_APP_SECRET" "$CDF_DEFAULT_APP_SECRET")"
    user_id="$(get_cdf_config_value "CDF_USER_ID" "$CDF_DEFAULT_USER_ID")"

    while (( $# > 0 )); do
        case "$1" in
            -ConversationID|--conversation-id)
                conversation_id="${2-}"
                shift 2
                ;;
            -TaskPrefix|--task-prefix)
                task_prefix="${2-}"
                shift 2
                ;;
            -StartTaskNumber|--start-task-number)
                start_task_number="${2-}"
                shift 2
                ;;
            -BaseUrl|--base-url)
                base_url="${2-}"
                shift 2
                ;;
            -AppId|--app-id)
                app_id="${2-}"
                shift 2
                ;;
            -AppSecret|--app-secret)
                app_secret="${2-}"
                shift 2
                ;;
            -UserId|--user-id)
                user_id="${2-}"
                shift 2
                ;;
            *)
                printf 'Unknown option for start_cdf_chat_interactive: %s\n' "$1" >&2
                return 1
                ;;
        esac
    done

    if ! [[ "$start_task_number" =~ ^[0-9]+$ ]]; then
        printf -- '-StartTaskNumber must be a non-negative integer.\n' >&2
        return 1
    fi

    if is_blank "$conversation_id"; then
        conversation_id="$(new_cdf_conversation_id)"
    fi

    task_number=$((start_task_number))
    if (( task_number < 1 )); then
        task_number=1
    fi

    print_stderr_color "36" "已进入交互测试模式。"
    print_stderr_color "36" "当前会话: $conversation_id"
    show_cdf_interactive_help >&2

    while true; do
        local task_id
        local input_text
        local response_json

        task_id="$(new_cdf_task_id "$task_prefix" "$task_number")"

        if ! IFS= read -r -p "[$conversation_id][$task_id] 你> " input_text; then
            printf '\n' >&2
            print_stderr_color "36" "已退出交互测试。"
            break
        fi

        input_text="$(trim_whitespace "${input_text//$'\r'/}")"
        if is_blank "$input_text"; then
            continue
        fi

        if [[ "$input_text" =~ ^/(exit|quit)$ ]]; then
            print_stderr_color "36" "已退出交互测试。"
            break
        fi

        if [[ "$input_text" == "/help" ]]; then
            show_cdf_interactive_help >&2
            continue
        fi

        if [[ "$input_text" == "/interrupt" ]]; then
            if response_json="$(
                invoke_cdf_interrupt \
                    -ConversationID "$conversation_id" \
                    -TaskId "$task_id" \
                    -BaseUrl "$base_url" \
                    -AppId "$app_id" \
                    -AppSecret "$app_secret" \
                    -UserId "$user_id"
            )"; then
                write_cdf_chat_response "$response_json"
            else
                print_stderr_color "31" "中断请求失败。"
            fi
            ((task_number++))
            continue
        fi

        if [[ "$input_text" =~ ^/new([[:space:]]+(.+))?$ ]]; then
            if [[ -n "${BASH_REMATCH[2]-}" ]]; then
                conversation_id="$(trim_whitespace "${BASH_REMATCH[2]}")"
            else
                conversation_id="$(new_cdf_conversation_id)"
            fi

            task_number=$((start_task_number))
            if (( task_number < 1 )); then
                task_number=1
            fi

            print_stderr_color "36" "已切换会话: $conversation_id"
            continue
        fi

        if response_json="$(
            invoke_cdf_chat \
                -ConversationID "$conversation_id" \
                -TaskId "$task_id" \
                -QueryText "$input_text" \
                -BaseUrl "$base_url" \
                -AppId "$app_id" \
                -AppSecret "$app_secret" \
                -UserId "$user_id"
        )"; then
            write_cdf_chat_response "$response_json"
        else
            print_stderr_color "31" "请求失败。"
        fi

        ((task_number++))
    done
}

show_cdf_script_usage() {
    printf '%s\n' 'Usage:'
    printf '%s\n' '  source ./test_chat.sh'
    printf '%s\n' '  E:/GIT/Git/bin/bash.exe ./test_chat.sh interactive'
    printf '%s\n' '  E:/GIT/Git/bin/bash.exe ./test_chat.sh interactive -ConversationID "gift001"'
    printf '%s\n' '  E:/GIT/Git/bin/bash.exe ./test_chat.sh chat -ConversationID "gift001" -TaskId "t1" -QueryText "开始送礼"'
    printf '%s\n' '  E:/GIT/Git/bin/bash.exe ./test_chat.sh interrupt -ConversationID "gift001" -TaskId "t2"'
    printf '%s\n' '  invoke_cdf_chat -ConversationID "gift001" -TaskId "t1" -QueryText "开始送礼"'
    printf '%s\n' '  invoke_cdf_interrupt -ConversationID "gift001" -TaskId "t3"'
    printf '%s\n' ''
    printf '%s\n' 'Optional environment variables:'
    printf '%s\n' '  CDF_BASE_URL'
    printf '%s\n' '  CDF_APP_ID'
    printf '%s\n' '  CDF_APP_SECRET'
    printf '%s\n' '  CDF_USER_ID'
    printf '%s\n' '  CDF_PYTHON_BIN'
}

run_chat_mode() {
    local conversation_id=""
    local task_id=""
    local query_text=""
    local chat_histories_json="[]"
    local task_prefix="task"
    local start_task_number=1
    local base_url=""
    local app_id=""
    local app_secret=""
    local user_id=""
    local response_json

    while (( $# > 0 )); do
        case "$1" in
            -ConversationID|--conversation-id)
                conversation_id="${2-}"
                shift 2
                ;;
            -TaskId|--task-id)
                task_id="${2-}"
                shift 2
                ;;
            -QueryText|--query-text)
                query_text="${2-}"
                shift 2
                ;;
            -ChatHistoriesJson|--chat-histories-json)
                chat_histories_json="${2-}"
                shift 2
                ;;
            -TaskPrefix|--task-prefix)
                task_prefix="${2-}"
                shift 2
                ;;
            -StartTaskNumber|--start-task-number)
                start_task_number="${2-}"
                shift 2
                ;;
            -BaseUrl|--base-url)
                base_url="${2-}"
                shift 2
                ;;
            -AppId|--app-id)
                app_id="${2-}"
                shift 2
                ;;
            -AppSecret|--app-secret)
                app_secret="${2-}"
                shift 2
                ;;
            -UserId|--user-id)
                user_id="${2-}"
                shift 2
                ;;
            *)
                printf 'Unknown option for chat mode: %s\n' "$1" >&2
                return 1
                ;;
        esac
    done

    if is_blank "$query_text"; then
        printf 'chat mode requires -QueryText.\n' >&2
        return 1
    fi

    if is_blank "$conversation_id"; then
        conversation_id="$(new_cdf_conversation_id)"
    fi

    if is_blank "$task_id"; then
        if ! [[ "$start_task_number" =~ ^[0-9]+$ ]]; then
            printf -- '-StartTaskNumber must be a non-negative integer.\n' >&2
            return 1
        fi
        local start_task_number_int
        start_task_number_int=$((start_task_number))
        if (( start_task_number_int < 1 )); then
            start_task_number_int=1
        fi
        task_id="$(new_cdf_task_id "$task_prefix" "$start_task_number_int")"
    fi

    local args=(
        -ConversationID "$conversation_id"
        -TaskId "$task_id"
        -QueryText "$query_text"
        -ChatHistoriesJson "$chat_histories_json"
    )

    if ! is_blank "$base_url"; then
        args+=(-BaseUrl "$base_url")
    fi
    if ! is_blank "$app_id"; then
        args+=(-AppId "$app_id")
    fi
    if ! is_blank "$app_secret"; then
        args+=(-AppSecret "$app_secret")
    fi
    if ! is_blank "$user_id"; then
        args+=(-UserId "$user_id")
    fi

    response_json="$(invoke_cdf_chat "${args[@]}")"
    print_json_pretty "$response_json"
}

run_interrupt_mode() {
    local conversation_id=""
    local task_id=""
    local task_prefix="task"
    local start_task_number=1
    local base_url=""
    local app_id=""
    local app_secret=""
    local user_id=""
    local response_json

    while (( $# > 0 )); do
        case "$1" in
            -ConversationID|--conversation-id)
                conversation_id="${2-}"
                shift 2
                ;;
            -TaskId|--task-id)
                task_id="${2-}"
                shift 2
                ;;
            -TaskPrefix|--task-prefix)
                task_prefix="${2-}"
                shift 2
                ;;
            -StartTaskNumber|--start-task-number)
                start_task_number="${2-}"
                shift 2
                ;;
            -BaseUrl|--base-url)
                base_url="${2-}"
                shift 2
                ;;
            -AppId|--app-id)
                app_id="${2-}"
                shift 2
                ;;
            -AppSecret|--app-secret)
                app_secret="${2-}"
                shift 2
                ;;
            -UserId|--user-id)
                user_id="${2-}"
                shift 2
                ;;
            *)
                printf 'Unknown option for interrupt mode: %s\n' "$1" >&2
                return 1
                ;;
        esac
    done

    if is_blank "$conversation_id"; then
        printf 'interrupt mode requires -ConversationID.\n' >&2
        return 1
    fi

    if is_blank "$task_id"; then
        if ! [[ "$start_task_number" =~ ^[0-9]+$ ]]; then
            printf -- '-StartTaskNumber must be a non-negative integer.\n' >&2
            return 1
        fi
        local start_task_number_int
        start_task_number_int=$((start_task_number))
        if (( start_task_number_int < 1 )); then
            start_task_number_int=1
        fi
        task_id="$(new_cdf_task_id "$task_prefix" "$start_task_number_int")"
    fi

    local args=(
        -ConversationID "$conversation_id"
        -TaskId "$task_id"
    )

    if ! is_blank "$base_url"; then
        args+=(-BaseUrl "$base_url")
    fi
    if ! is_blank "$app_id"; then
        args+=(-AppId "$app_id")
    fi
    if ! is_blank "$app_secret"; then
        args+=(-AppSecret "$app_secret")
    fi
    if ! is_blank "$user_id"; then
        args+=(-UserId "$user_id")
    fi

    response_json="$(invoke_cdf_interrupt "${args[@]}")"
    print_json_pretty "$response_json"
}

main() {
    local mode="${1:-usage}"
    shift || true

    case "$mode" in
        interactive)
            start_cdf_chat_interactive "$@"
            ;;
        chat)
            run_chat_mode "$@"
            ;;
        interrupt)
            run_interrupt_mode "$@"
            ;;
        usage|help|-h|--help|"")
            show_cdf_script_usage
            ;;
        *)
            printf 'Unknown mode: %s\n' "$mode" >&2
            show_cdf_script_usage >&2
            return 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

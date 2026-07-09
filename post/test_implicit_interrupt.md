# 隐式中断交互测试脚本使用说明

本文档说明如何使用 `test_implicit_interrupt.ps1` 测试 `/cdfai/v1/fudan/chat` 接口的隐式中断逻辑，包括本地服务启动、本地连接测试、远程服务连接测试。

## 脚本作用

`test_implicit_interrupt.ps1` 是一个 PowerShell 交互式测试脚本，用来模拟前端多轮对话。

每轮请求返回后，脚本会让测试人员选择：

- `a`：保存本轮回答到本地 `ChatHistories`，下一轮请求会携带本轮历史。
- `i`：丢弃本轮回答，不写入本地 `ChatHistories`，下一轮请求仍使用旧历史；服务端应据此推断上一轮被用户中断。
- `h`：查看当前本地 `ChatHistories`。
- `r`：查看本轮原始响应 JSON。
- `q`：退出脚本。

当接口返回推荐商品时，脚本会在原始返回上方额外提取展示：

- `pro-recommend` 中的商品名称、价格、商品 ID、图片地址。
- `add-questions` 或 `add_question` 中的追加问题。

## 前置条件（目前忽略）

脚本会自动计算接口签名，默认鉴权参数为：

```powershell
AppId     = cdf_26283b073aa0433a
AppSecret = 6c89a8e9a12b833ffefe0819b0db61c35229d023371f6f75667ebadc033d0ed4
UserId    = u100
```

如果远程环境使用不同鉴权参数，需要通过命令行参数或环境变量覆盖。

## 启动本地服务

### 方式一：直接用 Python 启动 `post` 服务（建议）

在项目根目录执行：

```powershell
cd .\post：进入post文件夹
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

说明：

- `test_implicit_interrupt.ps1` 默认连接 `http://127.0.0.1:8001`。
- 必须在 `post` 目录下启动 `uvicorn main:app`，否则 `main.py` 中的本地模块导入可能找不到。
- 如需指定大模型配置，可在 `post\.env` 中配置，或在启动前设置环境变量：

**（可以忽略）**
```powershell
$env:API_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:API_KEY = "<your-api-key>"
$env:TEXT_MODEL_NAME = "qwen3-max"
```

### 方式二：用 Docker 启动完整本地服务（目前忽略）

在项目根目录执行：

```powershell
docker build -t product-recommend .
docker run --rm -p 8000:8000 -p 8001:8001 -e API_KEY="<your-api-key>" product-recommend
```

根目录 `Dockerfile` 会同时复制 `cdf_api` 和 `post`，并通过根目录 `start.sh` 启动：

- `post` 服务：`8001`
- `cdf_api` 服务：`8000`

本测试脚本默认测试 `post` 服务，所以本地 Docker 启动后连接 `http://127.0.0.1:8001`。

## 连接本地服务并测试

打开新的 PowerShell 终端，在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\post\test_implicit_interrupt.ps1 -BaseUrl http://127.0.0.1:8001
```

也可以进入 `post` 目录执行：

```powershell
cd .\post
powershell -NoProfile -ExecutionPolicy Bypass -File .\test_implicit_interrupt.ps1 -BaseUrl http://127.0.0.1:8001
```

进入交互后，推荐按下面流程测试隐式中断：

1. 输入一个会触发推荐流程的问题，例如：

```text
我想买防晒霜
```

2. 服务返回商品推荐后，选择：

```text
i
```

这表示用户中断/丢弃本轮回答，本轮不会进入本地 `ChatHistories`。

3. 再输入一个新问题，例如：

```text
那口红有推荐吗
```

观察服务端是否能根据本次请求携带的 `ChatHistories` 推断上一轮被中断，并正确回滚或切换上下文。

4. 再测试正常保存路径：

```text
我想买防晒霜
```

返回后选择：

```text
a
```

下一轮请求会携带上一轮 `ChatHistories`，服务端应按连续多轮对话处理。

常用调试命令：

```text
/history   查看当前本地 ChatHistories
/clear     清空当前本地 ChatHistories
/new       新开随机会话
/new gift001 切换到指定会话并清空本地 ChatHistories
/exit      退出
```

返回后的选择项：

```text
a 保存本轮到 ChatHistories
i 中断/丢弃本轮
h 查看当前 ChatHistories
r 查看本轮原始响应 JSON
q 退出
```

## 连接远程服务并测试（重点）

远程服务只需要把 `-BaseUrl` 改为远程服务根地址，不要带接口路径。

连接命令（）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\post\test_implicit_interrupt.ps1 -BaseUrl http://product-recommend-8001-dev.cdfsunrise.com
```

脚本实际请求地址会自动拼接为：

```text
http://product-recommend-8001-dev.cdfsunrise.com/cdfai/v1/fudan/chat
```

如果远程环境使用不同的鉴权配置，使用参数传入 **（目前忽略）**：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\post\test_implicit_interrupt.ps1 `
  -BaseUrl https://example.com `
  -UserId u100 `
  -AppId "<remote-app-id>" `
  -AppSecret "<remote-app-secret>"
```

也可以使用环境变量，避免每次输入：

```powershell
$env:CDF_BASE_URL = "https://example.com"
$env:CDF_USER_ID = "u100"
$env:CDF_APP_ID = "<remote-app-id>"
$env:CDF_APP_SECRET = "<remote-app-secret>"

powershell -NoProfile -ExecutionPolicy Bypass -File .\post\test_implicit_interrupt.ps1
```

## 指定会话和任务编号

默认情况下，脚本会自动生成会话 ID，任务号从 `task001` 开始。

如需复现某个固定会话，可以指定：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\post\test_implicit_interrupt.ps1 `
  -BaseUrl http://127.0.0.1:8001 `
  -ConversationID gift001 `
  -TaskPrefix task `
  -StartTaskNumber 1
```

交互过程中也可以输入：

```text
/new gift001
```

这会切换到指定会话，并清空脚本本地保存的 `ChatHistories`。

## 查看签名字符串

如果远程服务返回签名校验失败，可以加 `-ShowSign` 查看脚本计算签名前的字符串和签名值：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\post\test_implicit_interrupt.ps1 `
  -BaseUrl https://example.com `
  -ShowSign
```

重点检查：

- `AppId` 和 `AppSecret` 是否与远程服务一致。
- 机器时间是否与服务端时间相差超过 5 分钟。
- `BaseUrl` 是否只填写服务根地址，而不是 `/cdfai/v1/fudan/chat` 完整接口地址。

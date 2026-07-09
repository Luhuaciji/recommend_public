# 中断处理改动说明

本文档只说明现在这版框架里：

- 需要改哪些文件
- 每个文件改什么
- 其他同学迁移时怎么改

当前版本的核心前提是：

- 没有专门的中断请求
- 所有请求都是正常请求
- 是否发生上一轮中断，靠下一轮请求带来的 `ChatHistories` 推断

判断规则很简单：

- 如果连续两次请求的 `ChatHistories` 完全相同
- 说明前端历史没有推进
- 可以推断上一轮没有被前端保存，也就是上一轮被中断

---

## 一、需要改的文件

这次主要改 4 个文件：

1. `main.py`
2. `api/schemas.py`
3. `core/session.py`
4. `core/agent_service.py`

---

## 二、每个文件改什么

### 1. `main.py`

`/cdfai-demo/v1/fudan/chat` 入口只保留正常请求。

现在不再做这些事：

- 不再接收显式中断请求
- 不再区分“正常请求”和“中断请求”

现在要做的事：

- 正常解析请求
- 直接调用 `handle_chat()`
- 把 `chatHistoriesSnapshot` 一起传进去

也就是说，入口层不再判断中断，中断推断放到业务层。

---

### 2. `api/schemas.py`

这里要补两个点：

#### 第一，生成 `ChatHistories` 的完整快照

新增字段：

```python
chatHistoriesSnapshot: str
```

作用：

- 保存本次请求里的完整 `ChatHistories`
- 供 `handle_chat()` 判断“前端历史是否推进”

#### 第二，清洗历史时继续保留 `query / answer` 内容

也就是把：

```python
{"role": "user", "content": ...}
{"role": "assistant", "content": ...}
```

这里不再依赖前端 `ChatHistories` 里的 `taskId`，因为智能小Q已经不再传这个字段。

---

### 3. `core/session.py`

这里主要加会话状态。

新增两个 session 字段：

- `lastFrontendChatHistoriesSnapshot`
- `lastUserTaskId`

作用分别是：

- `lastFrontendChatHistoriesSnapshot`
  保存上一轮请求看到的完整 `ChatHistories`

- `lastUserTaskId`
  保存上一轮我们自己正在处理的请求 `taskId`

这样下一轮请求进来时，就可以比较：

- 上一轮看到的 history 头
- 这一轮看到的 history 头

如果两次一样，就把上一轮 `lastUserTaskId` 视为被中断。

另外，`llm_history` 里统一使用 `taskId` 字段，不再混用 `task_id`。

---

### 4. `core/agent_service.py`

这是这次真正的核心改动。

#### 4.1 保留 `_append_interrupt_placeholder()`

这个函数继续保留，作用不变：

- 当某一轮被判定为中断时
- 在本地 history 里补一条中断占位

这样不会把这轮对话在本地上下文里丢掉。

#### 4.2 新增 `_should_infer_previous_interrupt()`

新增一个很小的辅助函数，专门判断：

- 当前请求是否说明“上一轮被中断”

判断依据就是：

```python
lastFrontendChatHistoriesSnapshot == chatHistoriesSnapshot
```

同时还要保证：

- `lastUserTaskId` 存在
- `lastUserTaskId != currentTaskId`

#### 4.3 调整 `handle_chat()` / `_handle_chat_unsafe()`

要把主流程改成下面这个思路：

##### 第 1 步：拿短锁，初始化 session，并先推断上一轮是否被中断

在锁内完成：

- 取/建 session
- 比较上一轮和当前轮的 `chatHistoriesSnapshot`
- 如果相同，就把 `lastUserTaskId` 标记为中断
- 同时补本地中断占位 history

##### 第 2 步：继续处理当前这一轮正常请求

注意：

- 当前这一轮不是中断请求
- 就算它隐含着“上一轮被中断”，它自己也仍然要继续正常执行

所以还要继续：

- 写入当前 user 消息
- 更新 `lastFrontendChatHistoriesSnapshot`
- 更新 `lastUserTaskId`

##### 第 3 步：锁外执行原来的业务逻辑

这一段业务逻辑一般不用改：

```python
agent_result = await _agent_brain(...)
```

各自业务分支可以继续保留。

##### 第 4 步：结果返回前，检查这一轮后来是否也被判成中断

如果当前轮后来也被后续请求判成中断：

- 本地 history 里补中断占位
- 不写正常 assistant 历史
- 但返回结果仍然正常返回给智能小Q

也就是说：

- 服务端负责维护本地 session/history 的一致性
- 智能小Q自己决定是否丢弃这次被中断请求的返回结果

##### 第 5 步：如果当前轮没有被判成中断，则正常收尾

继续保留原来的逻辑：

- 写 assistant 历史
- 裁剪历史
- 保存 session
- 返回业务结果

---

## 三、给其他同学的最短迁移方法

如果其他同学要把这套逻辑同步到自己的业务分支，建议优先按下面这个原则：

- 能直接复制的文件，就优先整文件复制
- 只有带明显业务逻辑的文件，才优先手工迁移

推荐做法：

1. 直接整文件复制 `main.py`
2. 直接整文件复制 `api/schemas.py`
3. 直接整文件复制 `core/session.py`
4. `core/agent_service.py` 不建议整文件复制
5. 在自己的 `agent_service.py` 里手工迁移中断相关逻辑

这样分的原因很简单：

- `main.py` 是通用入口层
- `api/schemas.py` 是通用协议层
- `core/session.py` 是通用会话层
- `core/agent_service.py` 往往混着各自同学自己的业务逻辑

所以迁移时最稳的方式是：

- `main.py` 直接复制
- `api/schemas.py` 直接复制
- `core/session.py` 直接复制
- `core/agent_service.py` 手工迁移

对于 `agent_service.py`，建议只迁移下面这些部分：

- `_append_interrupt_placeholder()`
- `_should_infer_previous_interrupt()`
- `handle_interrupt()` / `_handle_interrupt_unsafe()`
- `handle_chat()` / `_handle_chat_unsafe()` 的第 1、2、4、5 步

中间这段一般保留各自原有业务逻辑，不需要整体替换：

```python
agent_result = await _agent_brain(...)
```

---

## 四、最少验证用例

至少验证下面 3 个场景：

### 场景 1：第一轮正常到达，第二轮到达时 `ChatHistories` 没推进

预期：

- 第二轮进入时，能推断第一轮被中断
- 第一轮在本地 history 里被补成中断占位
- 第二轮自己仍然正常处理

### 场景 2：第一轮就是首轮，`ChatHistories` 为空；第二轮 `ChatHistories` 仍然为空

预期：

- 能推断第一轮被中断

### 场景 3：没有发生中断，第二轮 `ChatHistories` 已正常推进

预期：

- 不触发中断补记
- 正常写入 `user + assistant` 历史

---

## 五、迁移时的注意点

- 本地 history 统一使用 `taskId`，不要再混用 `task_id`
- `query_extends` 保留为框架扩展点，不要因为基础版本暂时没用就删掉
- `main.py`、`api/schemas.py`、`core/session.py` 优先整文件复制
- `agent_service.py` 优先手工迁移，不建议直接整文件覆盖

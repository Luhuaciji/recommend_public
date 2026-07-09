# 基础设施验证

本项目包含用于验证基础设施连通性的工具，确保大模型、数据库及缓存服务均可正常访问

### 1. 交付文件说明

* **`requirements.txt`**：项目运行所需的 Python 依赖包清单
* **`test_remote.py`**：集成测试脚本

---

### 2. 环境准备

建议使用 **Python 3.10** 环境。在终端中执行以下命令安装必要依赖：

```bash
pip install -r requirements.txt

```

---

### 3. 测试方法


```bash
python test.py

```

#### 验证覆盖项：

* **PostgreSQL**: 验证内网域名解析、账号认证及基础表 DDL/DML 权限。
* **Redis**: 验证内网域名解析及密码认证。
* **Elasticsearch**: 验证三节点集群在 `Security Mode` 下的认证联通。
* **DeepSeek API**: 验证 API Key 有效性及大模型响应。

---

### 4. 预期测试结果

若各项输出如下所示，则代表基础设施环境已完全 Ready：

* `✅ PG 成功`: 数据库连接及读写正常。
* `✅ Redis 成功`: 缓存认证及读写正常。
* `✅ ES 成功`: Elasticsearch 集群认证通过。
* `✅ DeepSeek 成功`: 大模型接口响应正常。

---
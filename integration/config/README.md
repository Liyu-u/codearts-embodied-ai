# integration/config —— local / sim / real 环境配置

本目录把执行环境拆成三档，供 executor 适配器按名选择后端与安全参数。

## 目录

```text
integration/config/
├── __init__.py            # 包入口，导出 load_profile / build_backend / list_profiles
├── models.py              # ExecutorProfile 数据模型
├── loader.py              # TOML 加载 + 环境变量覆盖 + 后端工厂
└── profiles/
    ├── local.toml         # 本地 Mock / CI
    ├── sim.toml           # 校内服务器 Isaac Sim 6.0.0
    └── real.toml          # 真机小范围低速测试
```

## 三档环境

| 名称 | backend | 用途 | 安全特征 |
|---|---|---|---|
| `local` | `mock` | 本地开发、Mock 联调、CI | 无人工确认，宽松工作空间 |
| `sim` | `isaac` | 服务器离线仿真 | 碰撞检查 fail-closed，限速 |
| `real` | `real` | 真机小范围验证 | 强制人工确认，缩窄工作空间，限速 ≤0.05 m/s，限力 |

## 用法

```python
from integration.config.loader import load_profile, build_backend, list_profiles

list_profiles()                  # ["local", "sim", "real"]
profile = load_profile("sim")    # ExecutorProfile(name="sim", backend="isaac", safety=...)
backend = build_backend(profile, perception_v1)
```

也可通过适配器一步完成：

```python
from integration.adapters.executor import ExecutorAdapter
executor = ExecutorAdapter.from_profile(load_profile("sim"), perception_v1)
```

## 环境变量覆盖

TOML 是默认值，运行时可用环境变量覆盖（优先级更高）：

| 变量 | 作用 |
|---|---|
| `RIA_DEPLOYMENT_DOMAIN` | `daily`（默认）或 `industrial`，决定读取哪组限速/限力 |
| `RIA_DAILY_MAX_VELOCITY_MS` / `RIA_INDUSTRIAL_MAX_VELOCITY_MS` | 覆盖线速度上限 |
| `RIA_DAILY_MAX_FORCE_N` / `RIA_INDUSTRIAL_MAX_FORCE_N` | 覆盖夹爪力上限 |
| `EXECUTOR_BACKEND` | 覆盖后端（`mock` / `isaac` / `real`） |

## 安全字段

每个 profile 的安全策略包含：

- `workspace`：世界坐标系立方体工作空间（x/y/z 上下限，单位米）；
- `motion`：线速度/角速度/力上限、动作超时、默认指令速度、抓取验证力；
- `safety`：`require_human_confirmation`（真机强制）、`e_stop_enabled`、
  `collision_check`、`fail_closed_on_error`（碰撞/驱动异常一律 fail-closed）。

安全守卫的具体行为见 [`modules/executor/README.md`](../modules/executor/README.md)。

## 约定

- 密钥只放 `.env`，禁止提交真实服务器地址、SSH 端口、账号或密码；
- 真机配置改动需 C 负责人确认，`real.toml` 的限速/工作空间只能收紧不能放宽；
- `integration/config` 只在标准库上运行（`tomllib` 为 Python 3.11+），不导入 Isaac Sim。

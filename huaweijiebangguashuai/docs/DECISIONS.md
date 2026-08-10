# 技术决策记录

> 记录所有重要的技术决策，确保项目方向一致

---

## 2026-07-17: Isaac Sim API 导入路径

**决策**：使用 `isaacsim.core.*` 而非 `omni.isaac.core.*`

**为什么**：Isaac Sim 6.0.1 (pip 安装版) 中 `omni.isaac.core` 命名空间不存在/已废弃。通过实际测试验证，正确的导入路径为：
- `isaacsim.core.api` → World, SimulationContext
- `isaacsim.core.experimental.prims` → Articulation, XFormPrim
- `isaacsim.core.utils.stage` → get_current_stage
- `isaacsim.core.utils.types` → ArticulationAction
- `isaacsim.storage.native` → get_assets_root_path

---

## 2026-07-17: 双模式架构 (Kit / Mock)

**决策**：所有 Isaac Sim 模块实现双模式运行

**为什么**：
- Kit 模式：仅在 `isaacsim.exe --exec` 运行时内可用，需要 GPU，冷启动 10 分钟
- Mock 模式：普通 Python 即可运行，用于单元测试和 CI/CD
- 通过 `_KIT_MODE = True/False` 标志位自动切换
- 安全断言（assert）在两种模式下均生效

**实现**：
```python
try:
    from isaacsim.core.api import World
    _KIT_MODE = True
except ImportError:
    _KIT_MODE = False
```

---

## 2026-07-17: 策略代码执行安全模型

**决策**：三层防护 + 命名空间隔离

**为什么**：CodeArts 生成的 Python 代码不可完全信任，必须经过安全校验后才能 exec()

**三层防护**：
1. 语法检查 (AST parse) — 拦截语法错误
2. 安全检查 (黑白名单) — 拦截 os/subprocess/eval 等危险调用
3. 物理断言 (safety assertions) — Z>=0.02, 关节限位, 夹爪力

**命名空间隔离**：只注入 9 个元 API + Python 内置，不暴露 `__builtins__` 中的危险函数

---

## 2026-07-17: 代码校验器 GBK 编码兼容

**决策**：移除 code_validator.py 中所有 emoji/Unicode 特殊字符，替换为 ASCII 标记

**为什么**：Windows 中文版控制台默认 GBK 编码，emoji (✅❌⚠️🚫) 会触发 `UnicodeEncodeError`。替换为 `[OK] [FAIL] [WARN] [BLOCK]` 等 ASCII 标记。

---

## 2026-07-17: Isaac Sim headless 运行方式

**决策**：使用 `isaacsim.exe --exec` 而非直接 `python` 运行脚本

**为什么**：Isaac Sim 6.0.1 的 Python API 只在 Kit 运行时内可用。必须先启动 Kit kernel，再在其中执行 Python 脚本。

**命令**：
```bash
set OMNI_KIT_ACCEPT_EULA=YES
isaacsim.exe --/app/headless=true --/renderer/type=fabric --exec script.py
```

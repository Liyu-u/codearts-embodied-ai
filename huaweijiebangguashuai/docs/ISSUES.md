# 问题记录

> 记录开发中遇到的 bug、坑、注意事项

---

## [已解决] GBK 编码错误 (2026-07-17)

**现象**：`UnicodeEncodeError: 'gbk' codec can't encode character '✅'`

**原因**：code_validator.py 中使用了 emoji 字符 (✅❌⚠️)，Windows 中文版 GBK 编码无法处理

**解决**：全部替换为 ASCII: `✅→[OK]`, `❌→[FAIL]`, `⚠️→[WARN]`, `🚫→[BLOCK]`

---

## [已解决] code_validator 白名单误报 (2026-07-17)

**现象**：`name.lower()` 被误报为 "未知函数调用"

**原因**：正则 `\b(\w+(\.\w+)?)\s*\(` 捕获了所有 `object.method()` 调用

**解决**：添加逻辑 `is_method = "." in call and call.split(".")[0][0].islower()`，允许对象方法调用

---

## [已解决] omni.isaac.core 导入失败 (2026-07-17)

**现象**：`from omni.isaac.core import ...` → `ModuleNotFoundError`

**原因**：Isaac Sim 6.0.1 (pip 版) 使用 `isaacsim.core.*` 命名空间

**解决**：全部改用 `isaacsim.core.api`, `isaacsim.core.experimental.prims` 等路径

---

## [已解决] trace_probe error_report 缺少 status 字段 (2026-07-17)

**现象**：`KeyError: 'status'` 在 dump_error_report 时

**原因**：`generate_error_report()` 返回的 dict 中缺少 `"status": "error"` 键

**解决**：在 `generate_error_report()` 错误分支中添加 `"status": "error"`

---

## [待解决] Isaac Sim headless 冷启动慢 (2026-07-17)

**现象**：首次启动 Isaac Sim 需要加载 100+ Kit 扩展，耗时约 10 分钟

**影响**：日常开发调试效率低

**可能方案**：
1. 使用 Kit 的 `--/exts/disable-all` + 手动启用必要扩展
2. 精简 `.kit` 配置文件，只加载必需扩展
3. 日常用 Mock 模式开发，仅联调时启动真实 Isaac Sim

---

## [注意事项] conda run 的 GBK 终端问题

**现象**：`conda run -n huawei python ...` 输出乱码

**解决**：直接用 `D:/.../envs/huawei/python.exe` 完整路径，避免 conda wrapper 的终端编码问题

# 项目进度

> 最后更新：2026-07-17
> 同学 C（吴昌庆）维护

## 进行中

- [ ] Isaac Sim headless 端到端测试（冷启动 ~10min，环境已验证）
- [ ] 与队友 A/B/D 联调测试

## 待办

- [ ] Isaac Sim Kit 模式端到端联调 (isaacsim.exe --exec run_simulation.py --scene stacking_cubes)
- [ ] 与队友 B 联调 CodeArts 生成的策略代码 → POST /api/code/execute
- [ ] 与队友 A 联调意图解析 → GET /api/scene/current
- [ ] 与队友 D 联调探针挂载和闭环反馈
- [ ] 全链路 MVP 贯通测试（A→B→C→D 闭环）

## 已完成

- [x] [7/20] scene_builder.py 创建 — 三标准场景定义 + Kit 模式构建器
- [x] [7/20] 3 个标准 .usda 场景文件创建 (stacking_cubes, cup_lineup, color_sorting)
- [x] [7/20] run_simulation.py 更新 — 接入 scene_builder, 新增 4 个场景任务
- [x] [7/20] action_library.py — 12 个高层动作封装
- [x] [7/20] perception_observation v1.0.0 格式升级
- [x] [7/20] 4 个 JSON 样例文件 (docs/samples/)
- [x] [7/17] Isaac Sim 6.0.1 环境验证通过（API 路径确认）
- [x] [7/17] 核心文件完成 (exec_wrapper, get_scene_json, code_loader)
- [x] [7/17] 55/55 单元测试通过
- [x] [7/17] server.py v0.2.0, API 端点可正常工作
- [x] [7/17] API_MANUAL.md — 元 API 使用手册
- [x] [7/17] 项目记忆系统初始化 (docs/*.md)
- [x] [7/14] Sprint 1 计划制定
- [x] [7/14] 元 API 白皮书完成
- [x] [7/14] 项目仓库骨架搭建

## 本周目标 (7/17-7/20)

```
M1 (接口冻结, 7/16) → M2 (模块测试, 7/18) → M3 (MVP贯通, 7/20)
```

同学 C 的 M1 + M2 目标已完成（接口文档 + 模块代码 + 场景文件 + 测试）。

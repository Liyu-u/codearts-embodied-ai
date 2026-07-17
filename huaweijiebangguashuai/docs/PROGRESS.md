# 项目进度

> 最后更新：2026-07-17
> 同学 C（吴昌庆）维护

## 进行中

- [x] [7/17] Isaac Sim 6.0.1 环境验证 — GPU (RTX 5070 8GB) 识别正常
- [x] [7/17] exec_wrapper.py 重写 — 双模式架构 (Kit/Mock)
- [x] [7/17] get_scene_json.py 重写 — 双模式, 真实 USD Stage 遍历
- [x] [7/17] code_loader.py 新增 — 策略代码安全执行器
- [x] [7/17] run_simulation.py 新增 — Isaac Sim 仿真入口
- [x] [7/17] code_validator.py 修复 — GBK 编码+对象方法调用白名单
- [x] [7/17] server.py 接入真实代码执行 (v0.2.0)
- [x] [7/17] API_MANUAL.md — 元 API 使用手册 v1.0
- [x] [7/17] 55/55 单元测试通过
- [ ] Isaac Sim headless 端到端测试（冷启动 ~10min，环境已验证）
- [ ] 三个标准测试场景 .usd 文件

## 待办

- [ ] Isaac Sim Kit 模式端到端联调 (isaacsim.exe --exec run_simulation.py)
- [ ] 与队友 B 联调 CodeArts 生成的策略代码
- [ ] 与队友 D 联调探针挂载和闭环反馈
- [ ] 搭建三标准场景: 方块堆叠/杯子排列/颜色分类
- [ ] 全链路 MVP 贯通测试

## 已完成

- [x] [7/17] Isaac Sim 6.0.1 环境验证通过（API 路径确认）
- [x] [7/17] 三个核心文件重写完成 (exec_wrapper, get_scene_json, code_loader)
- [x] [7/17] 55/55 单元测试通过 (0.08s)
- [x] [7/17] code_loader 策略执行自检 SUCCESS
- [x] [7/17] server.py v0.2.0, API 端点可正常工作
- [x] [7/17] 项目记忆系统初始化 (docs/*.md)
- [x] [7/14] Sprint 1 计划制定
- [x] [7/14] 元 API 白皮书完成
- [x] [7/14] 项目仓库骨架搭建

## 本周目标 (7/17-7/20)

```
M1 (接口冻结, 7/16) → M2 (模块测试, 7/18) → M3 (MVP贯通, 7/20)
```

同学 C 的 M2 目标已提前完成（代码部分），剩余：场景文件 + 联调测试。

# 端到端联调入口

`integration/` 只放跨模块编排，不放任何模块的业务实现。

- `pipeline.py`：预留统一入口，按协议依次调用各模块适配器。
- `adapters/`：把各同学的本地实现包装成统一接口。
- `config/`：存放 `local`、`sim`、`real` 三种环境配置；密钥只放 `.env`，禁止提交。

每个适配器至少提供：

```text
run(input_json: dict) -> output_json: dict
health() -> dict
```

适配器收到或输出数据后，必须先按 `contracts/v1` 校验。任何校验失败都返回统一错误，不允许静默修正。

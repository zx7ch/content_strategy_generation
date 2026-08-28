# Task 3.1-C 轻量质量评测

固定中文场景包由 5 个版本化模板展开为 100 篇笔记，覆盖通勤凉感同义表达、儿童运动
场景的支持/反向证据、qualifier 不兼容分区和标题表达轨。运行命令：

```bash
pytest -q tests/unit/test_content_research_marketing_quality.py
```

结果为 `1 passed`。exact-span precision/recall、track mapping macro-F1、cluster pairwise
precision/recall、contradiction precision/recall 和 citation correctness/completeness 均为
`100%`；编造 quote 或错误 lineage 为 `0`。unsupported causality 与 Trace secret redaction
由独立 verifier/安全回归覆盖，不冒充为本固定包的测量结果。

这是合成固定包上的确定性回归门槛，不代表线上自然分布准确率。真实 LLM、已认证 XHS
和 Research Embedding 的脱敏 canary 仍属于 3.1-D，不能用这份结果替代。

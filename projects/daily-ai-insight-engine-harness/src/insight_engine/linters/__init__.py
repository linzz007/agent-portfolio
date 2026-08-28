"""Stage 产物 linter。

每个文件对应一个 stage 的运行时产物检查规则。
linter 只负责根据合同判断产物是否合格，不负责执行 stage。

所有 linter 通过 stage_gates.py 的 LINTERS 字典注册，
再由 hook 系统的 evaluate_linter 监听器在 stage 执行后自动调用。
"""

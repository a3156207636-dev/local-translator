"""端到端验收：对着一组真实中文句子跑一遍，报告延迟与译文。

用法:
    .venv\\Scripts\\python.exe verify.py            # 用 config.json 里的引擎
    .venv\\Scripts\\python.exe verify.py --engine mock
"""
from __future__ import annotations

import argparse
import sys
import time

import config as cfgmod
import context_reader as ctxmod
import engines as engmod

SAMPLES = [
    "今天天气不错，我们出去走走吧。",
    "这个功能在离线环境下也能稳定运行。",
    "麻烦把这份文档翻译成英文，语气正式一点。",
    "我刚才提交了一个 bug 修复，麻烦你 review 一下。",
    "如果显存不够，可以把量化等级降到 Q4。",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["openai", "ollama", "mock"])
    ap.add_argument("--model")
    args = ap.parse_args()

    cfg = cfgmod.load()
    if args.engine:
        cfg["engine"]["type"] = args.engine
    if args.model:
        cfg["engine"]["model"] = args.model

    eng = engmod.build_engine(cfg["engine"])
    changed = engmod.autofit_model(cfg, eng)
    if changed:
        cfgmod.save(cfg)
        eng = engmod.build_engine(cfg["engine"])
    ok, msg = eng.health()
    print(f"引擎     : {eng.label}")
    print(f"地址     : {cfg['engine'].get('base_url')}")
    print(f"模型     : {cfg['engine'].get('model')}")
    print(f"目标语言 : {cfg['engine'].get('target_lang')} / 风格 {cfg['engine'].get('style')}")
    print(f"健康检查 : {'OK' if ok else '不可用'}  {msg}")
    print("-" * 72)
    if not ok and eng.label != "mock":
        print("引擎不可用，先启动本机翻译服务再跑。")
        return 1

    target = cfg["engine"].get("target_lang", "English")
    style = cfg["engine"].get("style", "natural")
    total = 0
    fail = 0
    for i, cn in enumerate(SAMPLES, 1):
        unit = ctxmod.extract_unit(cn)
        t0 = time.perf_counter()
        res = eng.run(unit, target, style)
        dt = (time.perf_counter() - t0) * 1000
        total += dt
        if res.ok:
            print(f"[{i}] {unit}")
            print(f"    → {res.dst}")
            print(f"    {dt:.0f} ms   ({res.engine})")
        else:
            fail += 1
            print(f"[{i}] {unit}")
            print(f"    ! 失败: {res.error}")
        print()

    print("-" * 72)
    print(f"共 {len(SAMPLES)} 条，成功 {len(SAMPLES) - fail}，失败 {fail}，"
          f"平均 {total / len(SAMPLES):.0f} ms/条")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

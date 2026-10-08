"""UI 噪声识别的回归测试。核心用例来自用户真实事故截图（抖音页面整页被翻译）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import context_reader as cr  # noqa: E402

# 用户截图事故的原始文本（抖音页面，无标点无换行的整页 dump）
DOUYIN_DUMP = (
    "请教一下大家 1月前 辽宁 12 分享 回复 展开2条回复 加载中 留下你的精彩评论吧 "
    "2.0万 305 1723 1.0万 听抖音 @白日一 （老鲁录屏） ·1周前 "
    "36分钟超长连线 老鲁见证了咸鱼之王的诞生 36分钟超长连线#硬件天空老鲁 #直播切片 "
    "汽水音乐：Monkeybiz 00:00 / 34:30 2.0x 超清 2K 清屏 连播"
)

CASES_PASS = [
    # 用户真正会打的字（必须能翻译）
    "请教一下大家",
    "这个方案我觉得风险有点大，我们能不能先跑一个小规模测试看看效果",
    "论文里的方法在低资源语言上表现不佳，主要原因是训练数据不足",
    "今天下午三点开会",
    # 含 UI 词但明显是正常句子（单个词不该误杀）
    "你回复我一下今天几点开会",
    "帮我把这份报告分享给团队",
    "这篇文章讲的是直播电商的供应链",
    # 无标点长句（真实输入场景）
    "这个方案我觉得风险有点大我们能不能先跑一个小规模测试看看效果再决定",
]

CASES_REJECT = [
    # 事故原文
    DOUYIN_DUMP,
    # 播放器界面（强特征：时间戳 + 倍速）
    "00:00 / 34:30 2.0x 超清 2K 清屏 连播 弹幕已关闭",
    # 评论区界面
    "展开2条回复 加载中 留下你的精彩评论吧 2.0万 305 1723 1.0万 分享 回复 举报",
    # 列表页
    "推荐 关注 热点 查看更多 打开App 下载App 1,723 播放量",
]


def main() -> int:
    ok = True
    print("== 应放行（用户真实输入）==")
    for t in CASES_PASS:
        r = cr.looks_like_ui_noise(t)
        mark = "OK  " if not r else "FAIL"
        if r:
            ok = False
        print(f"  [{mark}] noise={r}  {t[:36]}")

    print("== 应拦截（界面噪声）==")
    for t in CASES_REJECT:
        r = cr.looks_like_ui_noise(t)
        mark = "OK  " if r else "FAIL"
        if not r:
            ok = False
        print(f"  [{mark}] noise={r}  {t[:36]}")

    # extract_unit 回归：事故原文即使放进来，也要能被单例化（防御第二层）
    unit = cr.extract_unit(DOUYIN_DUMP, mode="last_sentence")
    print(f"  事故原文经 last_sentence 提取 -> {unit!r}")

    # 端到端：模拟 on_typing_paused 的完整判定链
    print("== 端到端判定链 ==")

    def would_translate(raw: str) -> bool:
        if not cr.has_cjk(raw):
            return False
        if cr.looks_like_ui_noise(raw):
            return False
        unit = cr.extract_unit(raw, mode="last_sentence", tail_chars=200, min_chars=2)
        return len(unit) >= 2 and cr.has_cjk(unit) and not cr.looks_like_ui_noise(unit)

    e2e_reject = [DOUYIN_DUMP,
                  "展开2条回复 加载中 2.0万 00:00 / 34:30 2.0x 超清"]
    e2e_pass = ["请教一下大家",
                "这个方案我觉得风险有点大，我们能不能先跑一个小规模测试看看效果"]
    for t in e2e_reject:
        r = would_translate(t)
        mark = "OK  " if not r else "FAIL"
        if r:
            ok = False
        print(f"  [{mark}] 不翻译  {t[:36]}")
    for t in e2e_pass:
        r = would_translate(t)
        mark = "OK  " if r else "FAIL"
        if not r:
            ok = False
        print(f"  [{mark}] 翻译    {t[:36]}")

    print("\n结果：", "全部通过" if ok else "存在失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

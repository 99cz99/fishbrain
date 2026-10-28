import sys
import time
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory import Store, decay_mood, mood_label, emotion_label, format_memory_line
from autonomy import Autonomy


class MoodTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.auto = Autonomy(self.store, Mock())

    # ---- decay_mood：双时间常数 + 不对称衰减 ----
    def test_decay_weak_returns_to_baseline(self):
        now = time.time()
        v, a, d, t = decay_mood(0.1, 0.1, 0.1, 0.1, now - 3600 * 20, now)
        self.assertLess(abs(v), 0.05)
        self.assertLess(abs(a), 0.05)
        self.assertLess(abs(d), 0.05)
        self.assertLess(abs(t), 0.05)

    def test_decay_strong_persists(self):
        now = time.time()
        v, a, d, t = decay_mood(0.8, 0.6, 0.7, 0.5, now - 3600 * 20, now)
        self.assertGreater(abs(v), 0.1)   # 强正情绪慢衰减，残留明显
        self.assertGreater(abs(a), 0.03)  # 双时间常数慢残留

    def test_decay_asymmetric_positive_slower_than_negative(self):
        now = time.time()
        vp, _, _, _ = decay_mood(0.8, 0.0, 0.0, 0.0, now - 3600 * 20, now)
        vn, _, _, _ = decay_mood(-0.8, 0.0, 0.0, 0.0, now - 3600 * 20, now)
        self.assertGreater(vp, abs(vn))   # 正情绪残留 > 负情绪残留

    def test_decay_no_time_gap_returns_original(self):
        now = time.time()
        self.assertEqual(decay_mood(0.5, 0.4, -0.3, 0.2, now, now), (0.5, 0.4, -0.3, 0.2))

    def test_decay_no_mood_at_returns_original(self):
        self.assertEqual(decay_mood(0.5, 0.4, -0.3, 0.2, 0, time.time()), (0.5, 0.4, -0.3, 0.2))

    # ---- mood_label：标签由四维唯一推导 ----
    def test_label_neutral_valence_is_neutral_even_with_extreme_dominance(self):
        # 边界空洞修复：dv 中性但 dd/dat 极端 → 仍是「如常」
        self.assertEqual(mood_label(0.0, 0.5, 0.9, 0.0), "如常")
        self.assertEqual(mood_label(0.0, 0.5, -0.9, 0.9), "如常")

    def test_label_positive(self):
        self.assertEqual(mood_label(0.6, 0.0, 0.0, 0.0), "开心")
        self.assertEqual(mood_label(0.3, 0.0, 0.0, 0.0), "不错")

    def test_label_negative_high_arousal_anger_guilt_fear(self):
        self.assertEqual(mood_label(-0.5, 0.7, 0.5, 0.0), "烦躁")  # 高 dominance → 愤怒
        self.assertEqual(mood_label(-0.5, 0.7, -0.5, 0.5), "内疚")  # 低 dominance + 自己致 → 内疚
        self.assertEqual(mood_label(-0.5, 0.7, -0.5, 0.0), "害怕")  # 低 dominance + 外界/中性 → 恐惧

    def test_label_negative_low_arousal(self):
        self.assertEqual(mood_label(-0.6, 0.1, 0.0, 0.0), "低落")
        self.assertEqual(mood_label(-0.3, 0.1, 0.0, 0.0), "有点闷")

    # ---- get_life_state：派生 mood + 含 dominance ----
    def test_life_state_derives_mood_and_has_dominance(self):
        self.store.set_life_state({"mood": "旧标签", "valence": 0.6, "arousal": 0.1, "dominance": 0.2, "mood_at": time.time()})
        st = self.store.get_life_state()
        self.assertIn("dominance", st)
        self.assertEqual(st["mood"], "开心")  # 忽略残留的旧 mood 字段，由数值派生

    # ---- _nudge_mood：三维 nudge + 双向 + mood_at ----
    def test_nudge_arousal_bidirectional(self):
        self.store.set_life_state({"valence": 0.0, "arousal": 0.5, "dominance": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood({"desirability": 0.0, "intensity": 0.5, "arousal": -1.0, "dominance": 0.0}, time.time())
        self.assertLess(self.store.get_life_state()["arousal"], 0.5)

    def test_nudge_dominance_offset(self):
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood({"desirability": 0.0, "intensity": 1.0, "arousal": 0.0, "dominance": 0.5}, time.time())
        self.assertGreater(self.store.get_life_state()["dominance"], 0.0)

    def test_nudge_attribution_offset(self):
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood({"desirability": 0.0, "intensity": 1.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.5}, time.time())
        self.assertGreater(self.store.get_life_state()["attribution"], 0.0)

    def test_nudge_saturation(self):
        app = {"desirability": 1.0, "intensity": 1.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0}
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood(app, time.time())
        gain0 = self.store.get_life_state()["valence"]
        self.store.set_life_state({"valence": 0.8, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood(app, time.time())
        gain8 = self.store.get_life_state()["valence"] - 0.8
        self.assertLess(gain8, gain0)  # 情绪已高时，同方向增量更小

    def test_nudge_uses_passed_mood_at(self):
        past = time.time() - 3600
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood({"desirability": 0.5, "intensity": 1.0, "arousal": 0.0, "dominance": 0.0}, past)
        self.assertEqual(self.store.get_settings()["life_state"]["mood_at"], past)

    def test_nudge_skips_below_min_intensity(self):
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood({"desirability": 1.0, "intensity": 0.1, "arousal": 1.0, "dominance": 1.0}, time.time())
        st = self.store.get_life_state()
        self.assertEqual(st["valence"], 0.0)
        self.assertEqual(st["dominance"], 0.0)

    # ---- emotion 绑定记忆（第二轮）----
    def test_emotion_label(self):
        self.assertEqual(emotion_label(0.5), "开心")
        self.assertEqual(emotion_label(0.3), "不错")
        self.assertEqual(emotion_label(0.1), "")
        self.assertEqual(emotion_label(-0.5), "低落")
        self.assertEqual(emotion_label(-0.3), "有点闷")

    def test_format_memory_line_annotates_emotion(self):
        m = {"kind": "fact", "subject": "小鱼", "content": "升职了", "emotion": 0.6}
        self.assertIn("当时开心", format_memory_line(m))
        self.assertIn("[fact]", format_memory_line(m, with_kind=True))
        self.assertNotIn("当时", format_memory_line({"subject": "小鱼", "content": "喝茶", "emotion": 0.0}))

    def test_insert_memory_stores_emotion(self):
        self.store.add_memory("fact", "小鱼", "升职了", emotion=0.6)
        row = self.store.get_memories(kind="fact")[0]
        self.assertAlmostEqual(row["emotion"], 0.6, places=3)

    def test_apply_episode_result_stores_emotion(self):
        event = self.store.record_episode("qq:1", "小鱼", "我升职了", "owner")
        self.store._conn.execute("UPDATE memories SET occurred_at=?", (time.time()-60,))
        self.store._conn.commit()
        self.store.apply_episode_result(
            self.store.pending_episode_batch(),
            {"memories": [{"kind": "fact", "content": "升职", "importance": 5, "emotion": 0.7, "source_ids": [event]}]},
        )
        row = self.store.get_memories(kind="fact")[0]
        self.assertAlmostEqual(row["emotion"], 0.7, places=3)

    def test_heartbeat_emotion_contagion(self):
        from life import Life
        brain = Mock()
        brain.chat_with_tools.return_value = {"tool_calls": []}
        life = Life(self.store, brain)
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "mood_at": time.time()})
        self.store.add_memory("fact", "小鱼", "升职了", emotion=0.8)
        life.heartbeat()
        self.assertGreater(self.store.get_life_state()["valence"], 0.0)

    def test_heartbeat_emotion_contagion_capped(self):
        from life import Life
        brain = Mock()
        brain.chat_with_tools.return_value = {"tool_calls": []}
        life = Life(self.store, brain)
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "mood_at": time.time()})
        for i in range(5):
            self.store.add_memory("fact", "小鱼", f"好事{i}", emotion=1.0)
        life.heartbeat()
        self.assertLessEqual(self.store.get_life_state()["valence"], 0.1)

    # ---- 外部信号（天气/昼夜）----
    def test_external_signal_weather_offsets_baseline(self):
        from unittest.mock import patch
        self.store.set_life_state({"valence": -0.5, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0, "mood_at": time.time() - 3600 * 20})
        self.store.set_setting("city", "北京")
        with patch("weather.get_weather_mood", return_value=-0.1):
            st = self.store.get_life_state()
            self.assertLess(st["valence"], -0.05)  # 20h 衰减后接近有效基线（-0.1）

    def test_contagion_weighted_by_importance(self):
        from life import Life
        brain = Mock()
        brain.chat_with_tools.return_value = {"tool_calls": []}
        life = Life(self.store, brain)
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0, "mood_at": time.time()})
        self.store.add_memory("fact", "小鱼", "重要好事", emotion=0.8, importance=10)
        self.store.add_memory("fact", "小鱼", "琐事", emotion=0.8, importance=1)
        life.heartbeat()
        # 加权：0.8*(10/10) + 0.8*(1/10) = 0.88，×0.05 = 0.044；平权会是 0.08
        self.assertAlmostEqual(self.store.get_life_state()["valence"], 0.044, places=2)

    # ---- mood_params：算法超参可配置 ----
    def test_mood_params_override_nudge(self):
        app = {"desirability": 1.0, "intensity": 1.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0}
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood(app, time.time())
        v_default = self.store.get_life_state()["valence"]
        self.store.set_setting("mood_params", {"appraisal_nudge_k": 0.8})
        self.store.set_life_state({"valence": 0.0, "arousal": 0.0, "dominance": 0.0, "attribution": 0.0, "mood_at": time.time()})
        self.auto._nudge_mood(app, time.time())
        v_override = self.store.get_life_state()["valence"]
        self.assertGreater(v_override, v_default)


if __name__ == '__main__':
    unittest.main()

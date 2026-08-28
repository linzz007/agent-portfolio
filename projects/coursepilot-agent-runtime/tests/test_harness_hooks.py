import unittest

from core.harness.hooks import LifecycleHooks
from core.harness.session import HarnessSession


class HarnessHooksTests(unittest.TestCase):
    def test_hook_order_and_handler_failure_isolated(self):
        seen = []

        def recorder(event):
            seen.append(event.name)

        def broken(_event):
            raise RuntimeError("hook failed")

        hooks = LifecycleHooks([recorder, broken])
        session = HarnessSession.create(
            course_name="course",
            mode="learn",
            skill_id="learn.answer.v1",
            user_message="hello",
        )

        first = hooks.emit("before_plan", session)
        second = hooks.emit("after_answer", session)

        self.assertEqual(["before_plan", "after_answer"], seen)
        self.assertEqual("before_plan", first.name)
        self.assertEqual("after_answer", second.name)


if __name__ == "__main__":
    unittest.main()

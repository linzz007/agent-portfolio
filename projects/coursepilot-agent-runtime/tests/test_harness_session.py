import unittest

from core.harness.session import HarnessSession, RunStatus


class HarnessSessionTests(unittest.TestCase):
    def test_session_lifecycle(self):
        session = HarnessSession.create(
            course_name="course",
            mode="learn",
            skill_id="learn.answer.v1",
            user_message="hello",
            trace_id="trace-1",
            request_id="req-1",
        )

        self.assertTrue(session.run_id.startswith("run_"))
        self.assertEqual(RunStatus.RUNNING, session.status)
        self.assertEqual("trace-1", session.trace_id)

        session.complete()
        self.assertEqual(RunStatus.SUCCEEDED, session.status)
        self.assertIsNotNone(session.ended_at)


if __name__ == "__main__":
    unittest.main()

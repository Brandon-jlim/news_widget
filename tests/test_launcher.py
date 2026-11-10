import contextlib
import io
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_pipeline import run_overlay


class LauncherTests(unittest.TestCase):
    def test_occupied_port_never_starts_a_collector(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            output = io.StringIO()
            with patch.object(sys, "argv", ["run_overlay", "--port", str(port)]), \
                    patch.object(run_overlay.subprocess, "Popen") as spawn, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(run_overlay.main(), 1)
            spawn.assert_not_called()
            self.assertIn("추가 수집기는 시작하지 않았습니다", output.getvalue())
            self.assertIn(f":{port}/?tab=benzinga", output.getvalue())

    def test_server_failure_never_starts_a_collector(self):
        server = Mock()
        server.poll.return_value = 1
        with patch.object(sys, "argv", ["run_overlay"]), \
                patch.object(run_overlay, "check_server_port", return_value=True), \
                patch.object(run_overlay.subprocess, "Popen", return_value=server) as spawn, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_overlay.main(), 1)
        self.assertEqual(spawn.call_count, 1)
        self.assertTrue(spawn.call_args.args[0][1].endswith("serve_overlay.py"))

    def test_server_is_ready_before_collection_and_benzinga_url_is_printed(self):
        server, relay = Mock(), Mock()
        events = []

        def spawn(command, **kwargs):
            events.append(Path(command[1]).name)
            return server if len(events) == 1 else relay

        def ready(*args):
            events.append("ready")
            return True

        output = io.StringIO()
        with patch.object(sys, "argv", ["run_overlay"]), \
                patch.object(run_overlay, "check_server_port", return_value=True), \
                patch.object(run_overlay.subprocess, "Popen", side_effect=spawn), \
                patch.object(run_overlay, "wait_for_server", side_effect=ready), \
                patch.object(run_overlay, "wait_for_exit", return_value=0), \
                contextlib.redirect_stdout(output):
            self.assertEqual(run_overlay.main(), 0)
        self.assertEqual(events, ["serve_overlay.py", "ready", "news_relay.py"])
        self.assertIn("?tab=benzinga", output.getvalue())
        self.assertIn("뉴스 API 호출 간격과 별개", output.getvalue())

    def test_relay_spawn_failure_stops_server(self):
        server = Mock()
        with patch.object(sys, "argv", ["run_overlay"]), \
                patch.object(run_overlay, "check_server_port", return_value=True), \
                patch.object(run_overlay, "wait_for_server", return_value=True), \
                patch.object(run_overlay.subprocess, "Popen", side_effect=[server, OSError("spawn failed")]), \
                patch.object(run_overlay, "terminate_process") as stop:
            with self.assertRaises(OSError):
                run_overlay.main()
        stop.assert_any_call(server)


if __name__ == "__main__":
    unittest.main()

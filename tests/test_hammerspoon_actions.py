import unittest
from unittest.mock import patch, MagicMock
from automation_agent.actions.hammerspoon import HammerspoonExecutor, ClickAction, TypeTextAction

class TestHammerspoonExecutor(unittest.TestCase):

    @patch("shutil.which")
    def test_executor_init_found(self, mock_which):
        mock_which.return_value = "/usr/bin/hs"
        executor = HammerspoonExecutor()
        self.assertTrue(executor.is_available())

    @patch("shutil.which")
    def test_executor_init_not_found(self, mock_which):
        mock_which.return_value = None
        executor = HammerspoonExecutor()
        self.assertFalse(executor.is_available())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_click_generation(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/hs"
        mock_run.return_value = MagicMock(stdout="ok", returncode=0)
        
        executor = HammerspoonExecutor()
        executor.click(100, 200)
        
        expected_lua = "hs.eventtap.leftClick({x=100, y=200})"
        mock_run.assert_called_with(
            ["/usr/bin/hs", "-c", expected_lua],
            capture_output=True, text=True, check=True
        )

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_type_text_escaping(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/hs"
        mock_run.return_value = MagicMock(stdout="ok", returncode=0)
        
        executor = HammerspoonExecutor()
        executor.type_text('Hello "World"')
        
        expected_lua = 'hs.eventtap.keyStrokes("Hello \\"World\\"")'
        mock_run.assert_called_with(
            ["/usr/bin/hs", "-c", expected_lua],
            capture_output=True, text=True, check=True
        )

class TestHammerspoonActionClasses(unittest.IsolatedAsyncioTestCase):
    
    @patch("automation_agent.actions.hammerspoon.HammerspoonExecutor.execute_lua")
    @patch("automation_agent.actions.hammerspoon.shutil.which")
    async def test_click_action_calls_executor(self, mock_which, mock_execute):
        mock_which.return_value = "/bin/hs"
        action = ClickAction(x=50, y=50)
        await action.execute()
        
        # Verify the executor was called with expected Lua
        args, _ = mock_execute.call_args
        self.assertIn("hs.eventtap.leftClick({x=50, y=50})", args[0])

if __name__ == "__main__":
    unittest.main()

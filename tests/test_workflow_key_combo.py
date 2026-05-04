import unittest

from app.core.workflow.executor import WorkflowExecutor


class WorkflowKeyComboTests(unittest.TestCase):
    def setUp(self):
        self.executor = WorkflowExecutor.__new__(WorkflowExecutor)

    def test_ctrl_k_uses_lowercase_letter_without_shift(self):
        self.assertEqual(self.executor._parse_key_combo("Ctrl+K"), ["Ctrl", "k"])

    def test_ctrl_shift_p_keeps_uppercase_letter(self):
        self.assertEqual(self.executor._parse_key_combo("Ctrl+Shift+P"), ["Ctrl", "Shift", "P"])

    def test_named_keys_keep_canonical_names(self):
        self.assertEqual(self.executor._parse_key_combo("ctrl+enter"), ["Ctrl", "Enter"])
        self.assertEqual(self.executor._parse_key_combo("command+k"), ["Meta", "k"])


if __name__ == "__main__":
    unittest.main()

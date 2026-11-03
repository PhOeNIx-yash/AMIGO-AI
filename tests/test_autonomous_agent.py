import os
import tempfile
import unittest
from amigo.core.task_agent import (
    AutonomousAgent,
    StepRecord,
    execute_command,
    execute_python,
    read_file_content,
    write_file_content,
    edit_file_content,
    list_dir_contents,
    decompose_task,
)
from amigo.utils.tool_registry import execute_tool, register_tool, TOOL_DEFINITIONS, UI_TOOL_HANDLERS


class TestUniversalPrimitives(unittest.TestCase):
    """Test the Turing-complete execution primitives for autonomous operation."""

    def test_execute_command_success(self):
        res = execute_command("echo 'Amigo Autonomous Agent'")
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("Amigo Autonomous Agent", res["stdout"])

    def test_execute_command_error(self):
        res = execute_command("non_existent_command_xyz_12345")
        self.assertFalse(res["success"])
        self.assertNotEqual(res["exit_code"], 0)

    def test_execute_python_success(self):
        code = "a = 21\nb = 2\nprint(f'Result={a * b}')"
        res = execute_python(code)
        self.assertTrue(res["success"])
        self.assertIn("Result=42", res["stdout"])

    def test_execute_python_error_captured(self):
        code = "raise ValueError('Custom error for testing')"
        res = execute_python(code)
        self.assertFalse(res["success"])
        self.assertIn("ValueError", res["stderr"])

    def test_file_operations_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test_doc.txt")

            # Write file
            w_res = write_file_content(fpath, "Line 1: Hello World\nLine 2: Autonomous Agent\nLine 3: Goodbye\n")
            self.assertTrue(w_res["success"])
            self.assertTrue(os.path.exists(fpath))

            # Read file
            r_res = read_file_content(fpath, start_line=1, end_line=2)
            self.assertTrue(r_res["success"])
            self.assertIn("Line 1", r_res["content"])
            self.assertIn("Line 2", r_res["content"])
            self.assertNotIn("Line 3", r_res["content"])

            # Edit file
            e_res = edit_file_content(fpath, "Autonomous Agent", "Fully Capable Agent")
            self.assertTrue(e_res["success"])
            self.assertEqual(e_res["replacements_made"], 1)

            # Verify edit
            r2_res = read_file_content(fpath)
            self.assertIn("Fully Capable Agent", r2_res["content"])

            # List directory
            l_res = list_dir_contents(tmpdir)
            self.assertTrue(l_res["success"])
            names = [it["name"] for it in l_res["items"]]
            self.assertIn("test_doc.txt", names)


class TestDynamicToolRegistry(unittest.TestCase):
    """Test dynamic tool registration without hardcoded schemas."""

    def test_dynamic_register_and_execute(self):
        custom_tool_name = "test_custom_math_tool"

        def _custom_handler(params, query, spoken):
            val = params.get("number", 0)
            return f"Computed: {val * 10}", None, {"computed": val * 10}

        register_tool(
            name=custom_tool_name,
            handler=_custom_handler,
            description="Multiplies a number by 10 dynamically.",
            parameters={"number": {"type": "integer", "description": "Number to multiply"}},
            required=["number"],
        )

        self.assertIn(custom_tool_name, UI_TOOL_HANDLERS)
        self.assertTrue(any(t["name"] == custom_tool_name for t in TOOL_DEFINITIONS))

        # Execute through standard execute_tool dispatcher
        spoken, url, meta = execute_tool(custom_tool_name, {"number": 5})
        self.assertEqual(spoken, "Computed: 50")
        self.assertEqual(meta.get("computed"), 50)


class TestAutonomousAgentStepParsing(unittest.TestCase):
    """Test ReAct step parsing and reasoning structures."""

    def test_parse_agent_step_action(self):
        agent = AutonomousAgent()
        json_output = '{"thought": "I need to check the files", "action": {"tool": "list_directory", "params": {"path": "."}}, "is_complete": false}'
        thought, tool, params, is_complete, final_answer = agent._parse_agent_step(json_output)
        self.assertEqual(tool, "list_directory")
        self.assertEqual(params.get("path"), ".")
        self.assertFalse(is_complete)
        self.assertIn("check the files", thought)

    def test_parse_agent_step_completion(self):
        agent = AutonomousAgent()
        json_output = '{"thought": "I have finished organizing the files", "action": null, "is_complete": true, "final_answer": "All files organized successfully."}'
        thought, tool, params, is_complete, final_answer = agent._parse_agent_step(json_output)
        self.assertTrue(is_complete)
        self.assertIsNone(tool)
        self.assertEqual(final_answer, "All files organized successfully.")

    def test_parse_agent_step_xml_tool_call(self):
        agent = AutonomousAgent()
        xml_output = """Thought: I will write the code to disk.
<tool_call>
<function=write_file>
<parameter=path>test_script.py</parameter>
<parameter=content>
print("Hello world")
def foo():
    return 'bar'
</parameter>
</function>
</tool_call>"""
        thought, tool, params, is_complete, final_answer = agent._parse_agent_step(xml_output)
        self.assertEqual(tool, "write_file")
        self.assertEqual(params.get("path"), "test_script.py")
        self.assertIn("def foo():", params.get("content", ""))
        self.assertFalse(is_complete)

    def test_parse_agent_step_malformed_json_content(self):
        agent = AutonomousAgent()
        malformed = '{"thought": "writing file", "action": {"tool": "write_file", "params": {"path": "out.txt", "content": "quoted "text" inside"}}, "is_complete": false}'
        thought, tool, params, is_complete, final_answer = agent._parse_agent_step(malformed)
        self.assertEqual(tool, "write_file")
        self.assertEqual(params.get("path"), "out.txt")

    def test_parse_agent_step_final_answer_cleanup(self):
        agent = AutonomousAgent()
        output = 'Thought: Finished the task.\nFinal Answer: Here is the summary of key changes.\n- Feature 1\n- Feature 2'
        thought, tool, params, is_complete, final_answer = agent._parse_agent_step(output)
        self.assertTrue(is_complete)
        self.assertIsNone(tool)
        self.assertIn("Here is the summary of key changes", final_answer)
        self.assertNotIn("Final Answer:", final_answer)


if __name__ == "__main__":
    unittest.main()


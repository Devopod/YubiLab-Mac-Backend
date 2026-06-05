import asyncio
import json
import uuid
import time
import os
import subprocess
from datetime import datetime
from worker.agent.dual_llm import get_llm_router
from worker.agent.solve_log import solve_log
from worker.agent.tools import AGENT_TOOLS
from worker.config import config
from worker.db.database import db

SYSTEM_PROMPT = """You are YubiDevin, an autonomous AI software engineer.
You build, test, debug, and deploy code autonomously.

RULES:
1. Plan first, then execute step by step
2. After each tool call, check the result before proceeding
3. If something fails, try a DIFFERENT approach (check past failures in context)
4. NEVER repeat the same failed approach
5. Always save working solutions by calling task_complete
6. Use browser tools to test web applications
7. Use terminal_exec to run commands and check output
8. Keep responses concise — you have limited context (4096 tokens)

AVAILABLE TOOLS:
- read_file, write_file, edit_file, delete_file, list_files
- terminal_exec, grep_search
- browser_navigate, browser_screenshot, browser_click, browser_fill, browser_assert
- rag_query, deploy_project, flutter_build
- spawn_sub_agent, task_complete

WORKFLOW:
1. Read existing files to understand the project
2. Plan your approach
3. Write/edit files
4. Run commands to test
5. Use browser to verify web apps
6. Fix any errors (try different approaches if needed)
7. Deploy if requested
8. Call task_complete when done
"""


class AgentOrchestrator:
    """Main autonomous agent that plans and executes tasks."""

    def __init__(self, workspace_id, user_id):
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.run_id = str(uuid.uuid4())
        self.llm = get_llm_router()
        self.workspace_path = os.path.join(
            config.WORKSPACE_BASE, workspace_id
        )
        self.active_sub_agents = 0
        self.max_sub_agents = 1
        self.messages = []
        self.step_count = 0
        self.running = False

    async def start(self, prompt, mode="autonomous", max_sub_agents=1):
        self.max_sub_agents = max_sub_agents
        self.running = True
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        # Ensure workspace exists
        os.makedirs(self.workspace_path, exist_ok=True)

        # Get solve log warnings
        warnings = await solve_log.get_warnings(
            self.workspace_id, prompt
        )
        if warnings:
            self.messages.append({
                "role": "system", "content": warnings
            })

        # Create run record
        await db.execute("""
            INSERT INTO agent_runs
            (id, user_id, workspace_id, prompt, mode, status, sub_agents_max)
            VALUES (?, ?, ?, ?, ?, 'running', ?)
        """, (self.run_id, self.user_id, self.workspace_id,
              prompt, mode, max_sub_agents))

        # Start execution loop
        asyncio.create_task(self._run_loop())

        return self.run_id

    async def _run_loop(self):
        """Main autonomous loop"""
        try:
            while self.running:
                self.step_count += 1

                # Check if messages are getting too long
                token_count = self.llm._count_tokens_approx(self.messages)
                if token_count > config.GROQ_SAFE_TOKENS:
                    self.messages = await self.llm._compress_messages(
                        self.messages
                    )

                # Call LLM
                response = await self.llm.complete(
                    messages=self.messages,
                    tools=AGENT_TOOLS
                )

                choice = response.choices[0]
                assistant_msg = choice.message

                # Add assistant message to history
                msg_dict = {"role": "assistant"}
                if assistant_msg.content:
                    msg_dict["content"] = assistant_msg.content
                if assistant_msg.tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in assistant_msg.tool_calls
                    ]
                self.messages.append(msg_dict)

                # If no tool calls, agent is done talking
                if not assistant_msg.tool_calls:
                    await self._complete(
                        summary=assistant_msg.content or "Task completed"
                    )
                    break

                # Execute tool calls
                for tool_call in assistant_msg.tool_calls:
                    if not self.running:
                        break

                    result = await self._execute_tool(tool_call)

                    # Add tool result to messages
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result[:2000]
                    })

                    # Update current step in DB
                    await db.execute("""
                        UPDATE agent_runs SET current_step = ?
                        WHERE id = ?
                    """, (f"{tool_call.function.name}: {result[:100]}", self.run_id))

                # Safety: max 50 steps to prevent infinite loop
                if self.step_count >= 50:
                    await self._complete(
                        summary="Max steps reached (50). Task may be incomplete."
                    )
                    break

        except Exception as e:
            await self._complete(error=str(e))

    async def _execute_tool(self, tool_call):
        """Execute a single tool call"""
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            return "Error: Invalid JSON arguments"

        start_time = time.time()

        try:
            if name == "read_file":
                result = await self._tool_read_file(args)
            elif name == "write_file":
                result = await self._tool_write_file(args)
            elif name == "edit_file":
                result = await self._tool_edit_file(args)
            elif name == "delete_file":
                result = await self._tool_delete_file(args)
            elif name == "list_files":
                result = await self._tool_list_files(args)
            elif name == "terminal_exec":
                result = await self._tool_terminal_exec(args)
            elif name == "grep_search":
                result = await self._tool_grep(args)
            elif name == "browser_navigate":
                result = await self._tool_browser_navigate(args)
            elif name == "browser_screenshot":
                result = await self._tool_browser_screenshot(args)
            elif name == "browser_click":
                result = await self._tool_browser_click(args)
            elif name == "browser_fill":
                result = await self._tool_browser_fill(args)
            elif name == "browser_assert":
                result = await self._tool_browser_assert(args)
            elif name == "rag_query":
                result = await self._tool_rag_query(args)
            elif name == "deploy_project":
                result = await self._tool_deploy(args)
            elif name == "flutter_build":
                result = await self._tool_flutter_build(args)
            elif name == "spawn_sub_agent":
                result = await self._tool_spawn_sub_agent(args)
            elif name == "task_complete":
                self.running = False
                result = "Task marked as complete"
            else:
                result = f"Unknown tool: {name}"

            duration = int((time.time() - start_time) * 1000)

            await solve_log.record(
                workspace_id=self.workspace_id,
                problem_type=name,
                problem=str(args)[:200],
                approach=name,
                tool_calls=[{"name": name, "args": args}],
                result_summary=str(result)[:200],
                success=True,
                agent_id=self.run_id,
                duration_ms=duration
            )

            return result

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            error_msg = str(e)

            await solve_log.record(
                workspace_id=self.workspace_id,
                problem_type=name,
                problem=str(args)[:200],
                approach=name,
                tool_calls=[{"name": name, "args": args}],
                error=error_msg,
                success=False,
                agent_id=self.run_id,
                duration_ms=duration
            )

            return f"Error: {error_msg}"

    # ─── File Tools ───

    async def _tool_read_file(self, args):
        path = os.path.join(self.workspace_path, args["path"])
        path = os.path.normpath(path)
        if not path.startswith(self.workspace_path):
            return "Error: Path traversal not allowed"
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            return content[:5000]
        except FileNotFoundError:
            return f"Error: File not found: {args['path']}"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _tool_write_file(self, args):
        path = os.path.join(self.workspace_path, args["path"])
        path = os.path.normpath(path)
        if not path.startswith(self.workspace_path):
            return "Error: Path traversal not allowed"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(args["content"])
        return f"File written: {args['path']} ({len(args['content'])} chars)"

    async def _tool_edit_file(self, args):
        path = os.path.join(self.workspace_path, args["path"])
        path = os.path.normpath(path)
        if not path.startswith(self.workspace_path):
            return "Error: Path traversal not allowed"
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if args["old_string"] not in content:
                return f"Error: old_string not found in {args['path']}"
            new_content = content.replace(
                args["old_string"], args["new_string"], 1
            )
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return f"File edited: {args['path']}"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _tool_delete_file(self, args):
        path = os.path.join(self.workspace_path, args["path"])
        path = os.path.normpath(path)
        if not path.startswith(self.workspace_path):
            return "Error: Path traversal not allowed"
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
        else:
            os.remove(path)
        return f"Deleted: {args['path']}"

    async def _tool_list_files(self, args):
        path = os.path.join(
            self.workspace_path, args.get("path", ".")
        )
        path = os.path.normpath(path)
        if not path.startswith(self.workspace_path):
            return "Error: Path traversal not allowed"

        if args.get("recursive"):
            result = []
            for root, dirs, files in os.walk(path):
                rel = os.path.relpath(root, self.workspace_path)
                for f in files:
                    fp = os.path.join(rel, f) if rel != "." else f
                    result.append(fp)
                if len(result) > 100:
                    result.append("... (truncated, 100+ files)")
                    break
            return "\n".join(result)
        else:
            items = os.listdir(path)
            return "\n".join(sorted(items))

    # ─── Terminal Tool ───

    async def _tool_terminal_exec(self, args):
        command = args["command"]
        timeout = args.get("timeout", 30)
        cwd = self.workspace_path

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            output = stdout.decode('utf-8', errors='replace')[-3000:]
            errors = stderr.decode('utf-8', errors='replace')[-1000:]

            if proc.returncode != 0:
                return f"Exit code: {proc.returncode}\nOutput: {output}\nStderr: {errors}"
            return output if output else "(no output)"
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _tool_grep(self, args):
        pattern = args["pattern"]
        path = os.path.join(
            self.workspace_path, args.get("path", ".")
        )
        file_type = args.get("file_type", "")

        cmd = f"grep -rn '{pattern}' '{path}'"
        if file_type:
            cmd += f" --include='*.{file_type}'"
        cmd += " | head -30"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            return stdout.decode('utf-8', errors='replace') or "No matches found"
        except Exception as e:
            return f"Error: {str(e)}"

    # ─── Browser Tools ───

    async def _tool_browser_navigate(self, args):
        try:
            from worker.browser.manager import get_browser
            browser = await get_browser()
            result = await browser.navigate(args["url"])
            await browser.close()
            return f"Navigated to {result['url']} (status: {result['status']}, title: {result['title']})"
        except ImportError:
            return "Error: Playwright not installed. Run: playwright install chromium"
        except Exception as e:
            return f"Browser error: {str(e)}"

    async def _tool_browser_screenshot(self, args):
        try:
            from worker.browser.manager import get_browser
            browser = await get_browser()
            img = await browser.screenshot(args.get("selector"))
            await browser.close()
            return f"Screenshot taken ({len(img)} bytes base64)"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _tool_browser_click(self, args):
        try:
            from worker.browser.manager import get_browser
            browser = await get_browser()
            await browser.click(args["selector"])
            await browser.close()
            return f"Clicked: {args['selector']}"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _tool_browser_fill(self, args):
        try:
            from worker.browser.manager import get_browser
            browser = await get_browser()
            await browser.fill(args["selector"], args["value"])
            await browser.close()
            return f"Filled {args['selector']} with value"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _tool_browser_assert(self, args):
        try:
            from worker.browser.manager import get_browser
            browser = await get_browser()
            passed = await browser.assert_page(
                args["assertion_type"], args["value"],
                args.get("selector")
            )
            await browser.close()
            return f"Assertion {'PASSED' if passed else 'FAILED'}: {args['assertion_type']} {args['value']}"
        except Exception as e:
            return f"Error: {str(e)}"

    # ─── RAG Tool ───

    async def _tool_rag_query(self, args):
        try:
            from worker.rag.engine import rag_engine
            results = await rag_engine.query(
                args["query"],
                workspace_id=self.workspace_id,
                top_k=args.get("top_k", 5)
            )
            if not results:
                return "No relevant documents found in knowledge base"
            return "\n---\n".join(
                f"[{r['source']}]\n{r['content'][:500]}"
                for r in results
            )
        except Exception as e:
            return f"RAG error: {str(e)}"

    # ─── Deploy Tool ───

    async def _tool_deploy(self, args):
        platform = args["platform"]
        project_path = os.path.join(
            self.workspace_path, args["project_path"]
        )

        if platform == "vercel":
            cmd = f"cd '{project_path}' && npx vercel --prod --yes 2>&1 | tail -20"
        elif platform == "netlify":
            cmd = f"cd '{project_path}' && npx netlify deploy --prod --yes 2>&1 | tail -20"
        elif platform == "render":
            return "Render deployment: Push to GitHub and connect repo on render.com"
        else:
            return f"Unknown platform: {platform}"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            return stdout.decode('utf-8', errors='replace') or "Deploy completed"
        except asyncio.TimeoutError:
            return "Deploy timed out after 120s"
        except Exception as e:
            return f"Deploy error: {str(e)}"

    # ─── Flutter Build Tool ───

    async def _tool_flutter_build(self, args):
        target = args["target"]
        project_path = os.path.join(
            self.workspace_path, args.get("project_path", ".")
        )

        if target == "web":
            cmd = f"cd '{project_path}' && flutter build web 2>&1 | tail -20"
        elif target == "apk":
            cmd = f"cd '{project_path}' && flutter build apk 2>&1 | tail -20"
        else:
            return f"Unknown target: {target}"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            return stdout.decode('utf-8', errors='replace')
        except asyncio.TimeoutError:
            return "Flutter build timed out after 300s"
        except Exception as e:
            return f"Build error: {str(e)}"

    # ─── Sub-Agent Tool ───

    async def _tool_spawn_sub_agent(self, args):
        if self.active_sub_agents >= self.max_sub_agents:
            return f"Error: Sub-agent limit reached ({self.max_sub_agents})"

        self.active_sub_agents += 1
        specialization = args["specialization"]
        task = args["task"]

        spec_prompts = {
            "code_writer": "You are a code writing specialist. Write clean, working code.",
            "tester": "You are a testing specialist. Write and run tests, verify correctness.",
            "debugger": "You are a debugging specialist. Analyze errors and fix code.",
            "deployer": "You are a deployment specialist. Deploy applications and verify they work."
        }

        sub = AgentOrchestrator(self.workspace_id, self.user_id)
        sub.max_sub_agents = max(0, self.max_sub_agents - 1)
        sub.run_id = f"sub-{uuid.uuid4()}"

        sub.messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + spec_prompts.get(specialization, "")},
            {"role": "user", "content": task}
        ]

        for _ in range(20):
            try:
                response = await self.llm.complete(
                    messages=sub.messages,
                    tools=AGENT_TOOLS
                )
                choice = response.choices[0]
                assistant_msg = choice.message

                msg_dict = {"role": "assistant"}
                if assistant_msg.content:
                    msg_dict["content"] = assistant_msg.content
                if assistant_msg.tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.id, "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in assistant_msg.tool_calls
                    ]
                sub.messages.append(msg_dict)

                if not assistant_msg.tool_calls:
                    break

                for tc in assistant_msg.tool_calls:
                    result = await sub._execute_tool(tc)
                    sub.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result[:2000]
                    })

                    if tc.function.name == "task_complete":
                        self.active_sub_agents -= 1
                        return f"Sub-agent ({specialization}) completed: {result[:500]}"
            except Exception:
                break

        self.active_sub_agents -= 1
        return f"Sub-agent ({specialization}) finished"

    async def _complete(self, summary="", error=None):
        self.running = False
        status = "completed" if not error else "failed"

        await db.execute("""
            UPDATE agent_runs
            SET status = ?, result_summary = ?, error = ?,
                steps_completed = ?, completed_at = datetime('now')
            WHERE id = ?
        """, (status, summary[:1000], error, self.step_count, self.run_id))

    async def stop(self):
        self.running = False
        await self._complete(summary="Stopped by user")

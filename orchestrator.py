#!/usr/bin/env python3
"""
child_repo/orchestrator.py

Self-healing script that:
 - Reads fail_logs.txt + existing conversation_log.md
 - Calls GPT with logs + the entire code
 - GPT can produce multi-file, multi-branch changes,
   plus a summary= block to show the user.
 - Writes conversation_log.md with the new summary
"""

import os
import re
import openai
import base64
import json
import subprocess

# If you want to store conversation logs in a separate file instead of .md,
# just change the path below:
CONVERSATION_LOG = "conversation_log.md"

# Master instructions for GPT: we want multiple files, branches, Docker, etc.
MASTER_SYSTEM_PROMPT = """REPLACE_THIS_AT_RUNTIME"""

def parse_gpt_output(full_text):
    """
    Look for code blocks of these forms:
      ```branch=BRANCHNAME
      path=some/file.txt
      ... file contents ...
      ```
    or
      ```run
      some CLI commands
      ```
    and a summary=... line.

    Returns:
      - A list of dicts describing changes or commands
        e.g. {"type": "file", "branch": "backend", "path": "backend/app.py", "content": "..."}
             {"type": "run", "commands": "..."}
      - A string summary
    """
    changes = []
    summary_text = ""

    # 1) Parse all code fences
    # We’ll look for triple backticks plus optional branch=, path=, etc.
    blocks = re.findall(r"```(.*?)```", full_text, re.DOTALL)
    for block in blocks:
        first_line, *rest = block.split('\n', 1)
        if len(rest) == 0:
            body = ""
        else:
            body = rest[0]

        # e.g. first_line could be: branch=frontend, path=frontend/src/App.js
        if first_line.strip().startswith("run"):
            # This is a CLI commands block
            changes.append({
                "type": "run",
                "commands": body.strip()
            })
        elif first_line.strip().startswith("branch="):
            # parse branch=..., maybe path=...
            # We can further parse the first_line to find branch=, path=, etc.
            # Example: "branch=frontend, path=frontend/src/App.js"
            # Let's just do a quick find all
            # branch=frontend => branch, path=some/file => path
            branch_match = re.search(r'branch\s*=\s*([^\s,]+)', first_line)
            path_match = re.search(r'path\s*=\s*([^\s,]+)', first_line)
            if branch_match and path_match:
                br = branch_match.group(1).strip()
                pt = path_match.group(1).strip()
                changes.append({
                    "type": "file",
                    "branch": br,
                    "path": pt,
                    "content": body
                })
        else:
            # ignore
            pass

    # 2) Parse summary= block
    # We'll do a simpler approach: look for "summary=" up to end of line
    summary_match = re.search(r'summary\s*=\s*(.*)', full_text)
    if summary_match:
        summary_text = summary_match.group(1).strip()

    return changes, summary_text

def main():
    openai.api_key = os.getenv("OPENAI_API_KEY_DECLAN", "")
    if not openai.api_key:
        print("[child orchestrator] No OPENAI_API_KEY_DECLAN, cannot self-heal.")
        return

    # We'll replace the placeholder with the actual MASTER_SYSTEM_PROMPT from environment
    system_prompt_env = os.getenv("MASTER_SYSTEM_PROMPT_OVERRIDE", "")
    system_prompt = system_prompt_env if system_prompt_env else MASTER_SYSTEM_PROMPT

    # gather logs
    LOG_FILE = "fail_logs.txt"
    if not os.path.exists(LOG_FILE):
        print("[child orchestrator] No fail_logs.txt found, nothing to fix.")
        return

    logs = ""
    with open(LOG_FILE, "r") as f:
        logs = f.read()

    # gather existing conversation log if any
    conversation_so_far = ""
    if os.path.exists(CONVERSATION_LOG):
        with open(CONVERSATION_LOG, "r") as f:
            conversation_so_far = f.read()

    # Construct user message
    user_msg = f"""
Here are the current fail logs, the entire conversation so far, and all code from the repo:

[FAIL LOGS]
{logs}

[CONVERSATION LOG so far]
{conversation_so_far}

Remember to produce a summary= block for the user,
and code blocks of the form:
```branch=BRANCHNAME
path=some/file.py
... your code ...
```
or
```run
some shell commands
```
(Any text outside code blocks is ignored except for 'summary=' lines.)
"""

    # GPT call
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=1900
        )
        content = resp["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[child orchestrator] GPT call failed: {e}")
        return

    # parse GPT output
    changes, summary_text = parse_gpt_output(content)
    print("[child orchestrator] GPT returned the following changes + summary:")
    print(changes)
    print("Summary:", summary_text)

    # Update conversation_log.md with the new summary
    with open(CONVERSATION_LOG, "a") as f:
        f.write("\n\n## GPT Update\n")
        f.write(f"{summary_text}\n")

    # Now apply changes
    for c in changes:
        if c["type"] == "file":
            branch = c["branch"]
            path = c["path"]
            file_content = c["content"]
            # Make sure we have the branch
            # We'll do a local commit/checkout approach
            # Typically we'd do a shell call: git checkout or create the branch
            # Then commit the file, push, etc.
            try:
                # checkout or create branch
                subprocess.run(["git", "checkout", branch], check=False)
                # if fails, create branch from main
                current_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
                if current_branch != branch:
                    # create branch
                    subprocess.run(["git", "checkout", "-b", branch], check=True)
                # write file
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(file_content)
                # commit
                subprocess.run(["git", "add", path], check=True)
                subprocess.run(["git", "commit", "-m", f"Update {path} [skip ci]"], check=True)
                # push
                subprocess.run(["git", "push", "--set-upstream", "origin", branch, "--force"], check=True)
                print(f"[child orchestrator] Committed file '{path}' to branch '{branch}'.")
            except Exception as e:
                print(f"[child orchestrator] Error updating {path} on branch {branch}: {e}")

        elif c["type"] == "run":
            commands = c["commands"]
            print("[child orchestrator] GPT wants to run commands:\n", commands)
            # You could decide whether to run them automatically or store them for manual execution.
            # For safety, let's just log them for now. If you trust GPT, uncomment the shell execution below:
            # try:
            #     subprocess.run(commands, shell=True, check=True)
            # except Exception as e:
            #     print("[child orchestrator] Error running GPT commands:", e)

if __name__ == "__main__":
    main()

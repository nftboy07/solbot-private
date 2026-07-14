import os
import aiohttp
import logging
import asyncio
import re
import tempfile
import shutil
import subprocess
from typing import Optional, Tuple, List

logger = logging.getLogger("bot.auditor")

class SolanaAuditor:
    """
    Asynchronous security auditor for Solana Rust/Anchor programs
    utilizing the Gemini and fallback LLM APIs.
    """
    def __init__(self, config):
        self._config = config
        self._gemini_key = getattr(config.ai, "gemini_api_key", None)
        self._groq_key = getattr(config, "GROQ_API_KEY", os.environ.get("GROQ_API_KEY"))

        # Primary Solana attack vectors for prompt injection
        self.attack_vectors = """
### SOLANA VULNERABILITY VECTORS:
1. **Missing Signer Check**: Check if accounts representing the signer are validated via `account.is_signer` or Anchor's `Signer<'info>` account type.
2. **Missing Owner Check**: Check if account ownership is validated (e.g., checking if the owner is the expected program ID, or using Anchor's `Account<'info, T>` wrappers).
3. **Integer Underflow/Overflow**: Review math operations (`+`, `-`, `*`, `/`). Verify that checked math (`checked_add`, `checked_sub`, `checked_mul`, `checked_div`) is used.
4. **Reentrancy / CPI Checks-Effects-Interactions**: Check if state modifications occur after calling Cross-Program Instructions (CPI). Verify CPI calls validate input program IDs.
5. **PDA Derivation Bypass**: Verify Program Derived Addresses (PDAs) are validated using expected seeds. Check if `Pubkey::create_program_address` or Anchor constraints `seeds` / `bump` are used correctly.
6. **Arbitrary CPI**: Check if target program addresses passed in instructions are validated against trusted program IDs to prevent arbitrary code execution.
7. **Arithmetic Precision Loss**: Check if multiplication is done before division to avoid round-down precision loss.
8. **Missing Rent Exemption Checks**: Ensure initialized accounts are checked for rent exemption.
"""

    async def _query_gemini(self, prompt: str) -> Optional[str]:
        if not self._gemini_key:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self._gemini_key}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        headers = {"Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    else:
                        error_text = await resp.text()
                        logger.error(f"Gemini Auditor API error: {resp.status} - {error_text}")
        except Exception as e:
            logger.error(f"Gemini Auditor API call failed: {e}")
        return None

    async def _query_groq(self, prompt: str) -> Optional[str]:
        # Bypasses to Groq if Gemini is rate limited or key is missing
        key = self._groq_key or os.environ.get("GROQ_API_KEY")
        if not key:
            return None
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "llama-3.1-70b-versatile",
            "messages": [
                {"role": "system", "content": "You are a professional Solana smart contract security auditor."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Groq Auditor API call failed: {e}")
        return None

    async def audit_code(self, source_name: str, code_content: str) -> str:
        """Runs a complete security review on a source file using the auditor pipeline."""
        prompt = f"""
You are the Solana smart contract security auditor. Analyze the following source file:
File: `{source_name}`

{self.attack_vectors}

### CODE TO AUDIT:
```rust
{code_content}
```

### AUDIT INSTRUCTIONS:
1. Scan for each of the Solana vulnerability vectors.
2. Group your findings into High, Medium, and Low severity.
3. For each finding, list:
   - Vulnerable code snippet
   - Explanation of the bug and threat impact
   - Recommended secure code fix
4. If the code is a Python/other script, audit it for general safety vulnerabilities, RPC leaks, or logic errors.

Be thorough, precise, and format your response in clean markdown.
"""
        # Try Gemini first
        res = await self._query_gemini(prompt)
        if res:
            return res
            
        # Try Groq fallback
        res = await self._query_groq(prompt)
        if res:
            return res

        return "❌ **Auditor Error**: Both Gemini and Groq API calls failed or were unconfigured."

    async def audit_github_repo(self, repo_url: str, progress_callback=None) -> Tuple[bool, str]:
        """Clones a remote git repository, collects all in-scope Rust files, and generates a merged audit report."""
        if progress_callback:
            await progress_callback("⏳ **Cloning remote GitHub repository...**")
            
        with tempfile.TemporaryDirectory() as tmpdir:
            # Clone repo
            try:
                # Use subprocess to run clone
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--depth", "1", repo_url, tmpdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    return False, f"Failed to clone repository: {stderr.decode('utf-8', errors='ignore')}"
            except Exception as e:
                return False, f"Git clone exception: {e}"

            if progress_callback:
                await progress_callback("🔍 **Searching for Rust program source files...**")

            # Collect in-scope Rust files (exclude test/target dirs)
            rust_files = []
            exclude_patterns = ["/target/", "/tests/", "/test/", "/migrations/", "/node_modules/"]
            
            for root, dirs, files in os.walk(tmpdir):
                # Apply exclusions
                if any(p in root.replace("\\", "/") for p in exclude_patterns):
                    continue
                for f in files:
                    if f.endswith(".rs") and not f.endswith("_test.rs") and not f.endswith("_tests.rs"):
                        rust_files.append(os.path.join(root, f))

            if not rust_files:
                return False, "No in-scope Solana Rust source files (`.rs`) found in repository."

            # Merge files into a single bundle
            bundle_content = []
            total_lines = 0
            for filepath in rust_files[:5]: # Limit to 5 files to fit prompt size limits safely
                rel_path = os.path.relpath(filepath, tmpdir)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        total_lines += len(lines)
                        bundle_content.append(f"### File: `{rel_path}`\n```rust\n" + "".join(lines) + "\n```\n")
                except Exception as e:
                    logger.error(f"Error reading file {rel_path} for audit: {e}")

            if progress_callback:
                await progress_callback(f"🧠 **Running security audit on {len(rust_files)} source files ({total_lines} lines of code)...**")

            # Audit the merged bundle
            audit_report = await self.audit_code(repo_url, "\n\n".join(bundle_content))
            return True, audit_report

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .io import ROOT, load_text, load_yaml


PROVIDER_PRESETS = {
    "chatgpt": {
        "mode": "cli",
        "binary": "codex",
    },
    "gemini": {
        "mode": "cli",
        "binary": "gemini",
    },
    "kimi": {
        "mode": "api",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "model": "kimi-k2.5",
    },
    "deepseek": {
        "mode": "api",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-v4-pro",
    },
    "xiaomi_mimo": {
        "mode": "api",
        "base_url": "https://api.xiaomimimo.com/v1",
        "api_key_env": "MIMO_API_KEY",
        "model": "mimo-v2.5-pro",
    },
}

GEMINI_API_ENV_KEYS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
GEMINI_AUTH_FILES = (
    "oauth_creds.json",
    "google_accounts.json",
    "installation_id",
    "trustedFolders.json",
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def extract_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Empty LLM response.")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    if "```" in stripped:
        import re

        match = re.search(r"```(?:json)?\s*(.+?)\s*```", stripped, re.S)
        if match:
            return json.loads(match.group(1))
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ValueError("Failed to parse JSON from LLM response.")


class LLMClient:
    def __init__(self, provider: str = "auto", runtime_path: Optional[Path] = None):
        local_env = ROOT / ".env"
        load_env_file(local_env if local_env.exists() else ROOT.parent / ".env")
        runtime = load_yaml(runtime_path or ROOT / "configs/runtime.yaml")
        config = runtime.get("llm", {})
        self.provider = provider if provider and provider != "auto" else config.get("provider", "chatgpt")
        preset = self._resolve_provider_preset(self.provider)
        self.mode = preset.get("mode", "api")
        self.binary = preset.get("binary", "")
        self.base_url = (config.get("base_url") or preset.get("base_url", "")).rstrip("/")
        self.model = config.get("model") or preset.get("model", "")
        self.api_key_env = config.get("api_key_env") or preset.get("api_key_env", "")
        self.api_key = os.environ.get(self.api_key_env, "")
        self.timeout = int(config.get("timeout_seconds", 120))
        self.temperature = float(config.get("temperature", 0.1))
        self.max_retries = int(config.get("max_retries", 3))
        self.retry_base_delay = float(config.get("retry_base_delay_seconds", 2.0))
        if self.mode == "cli":
            if not self.binary:
                raise RuntimeError(f"Missing CLI binary config for provider={self.provider!r}.")
            if shutil.which(self.binary) is None:
                raise RuntimeError(f"CLI provider {self.provider!r} requires {self.binary!r} on PATH.")
            return
        if not self.base_url or not self.model:
            raise RuntimeError(f"Missing LLM config for provider={self.provider!r}.")
        if not self.api_key:
            raise RuntimeError(f"Missing API key. Set {self.api_key_env} in project .env.")

    def _resolve_provider_preset(self, provider: str) -> Dict[str, Any]:
        if provider == "chatgpt" and os.environ.get("OPENAI_API_KEY", ""):
            return {
                "mode": "api",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "model": "gpt-5.4",
            }
        if provider == "gemini" and os.environ.get("GEMINI_API_KEY", ""):
            return {
                "mode": "api",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "api_key_env": "GEMINI_API_KEY",
                "model": "gemini-3-flash-preview",
            }
        return dict(PROVIDER_PRESETS.get(provider, {}))

    def _headers(self) -> Dict[str, str]:
        if self.provider == "xiaomi_mimo":
            return {
                "api-key": self.api_key,
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, system_prompt: str, user_payload: Dict[str, Any], temperature: Optional[float] = None) -> str:
        if self.mode == "cli":
            return self._chat_cli(system_prompt, user_payload)
        try:
            return self._chat_api(system_prompt, user_payload, temperature=temperature)
        except Exception as exc:
            if self.provider == "chatgpt" and os.environ.get("OPENAI_API_KEY", ""):
                return self._chat_cli_fallback("codex", system_prompt, user_payload, exc)
            if self.provider == "gemini" and os.environ.get("GEMINI_API_KEY", ""):
                return self._chat_cli_fallback("gemini", system_prompt, user_payload, exc)
            raise

    def _chat_cli_fallback(self, binary: str, system_prompt: str, user_payload: Dict[str, Any], api_error: Exception) -> str:
        if shutil.which(binary) is None:
            raise RuntimeError(f"API call failed and CLI fallback {binary!r} is not on PATH. API error: {api_error}")
        old_mode = self.mode
        old_binary = self.binary
        self.mode = "cli"
        self.binary = binary
        try:
            return self._chat_cli(system_prompt, user_payload)
        finally:
            self.mode = old_mode
            self.binary = old_binary

    def _chat_api(self, system_prompt: str, user_payload: Dict[str, Any], temperature: Optional[float] = None) -> str:
        request_temperature = self.temperature if temperature is None else temperature
        if self.provider == "kimi" and self.model.startswith("kimi-k2"):
            # Moonshot kimi-k2 models currently reject arbitrary temperatures.
            request_temperature = 1.0
        if self.provider == "xiaomi_mimo":
            request_temperature = 1.0
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
            ],
            "temperature": request_temperature,
            "response_format": {"type": "json_object"},
        }
        if self.provider == "deepseek" and self.model == "deepseek-v4-pro":
            payload["reasoning_effort"] = "high"
            payload["thinking"] = {"type": "enabled"}
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code < 400:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
                last_error = RuntimeError(f"{response.status_code}: {response.text}")
            except Exception as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(self.retry_base_delay * (2**attempt))
        raise last_error

    def _cli_prompt(self, system_prompt: str, user_payload: Dict[str, Any]) -> str:
        return "\n\n".join(
            [
                system_prompt.strip(),
                "Return only the requested final answer. If the task requests JSON, output only valid JSON.",
                "User payload:",
                json.dumps(user_payload, ensure_ascii=False, indent=2),
            ]
        )

    def _chat_cli(self, system_prompt: str, user_payload: Dict[str, Any]) -> str:
        prompt = self._cli_prompt(system_prompt, user_payload)
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._run_cli_prompt(prompt)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_base_delay * (2**attempt))
        raise last_error

    def _run_cli_prompt(self, prompt: str) -> str:
        if self.provider == "chatgpt":
            return self._run_codex_prompt(prompt)
        if self.provider == "gemini":
            return self._run_gemini_prompt(prompt)
        raise RuntimeError(f"Unsupported CLI provider={self.provider!r}.")

    def _run_codex_prompt(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="survey_agent_codex_") as tmpdir:
            output_file = Path(tmpdir) / "last_message.txt"
            command = [
                self.binary,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "-C",
                str(ROOT.parent),
                "--output-last-message",
                str(output_file),
                prompt,
            ]
            completed = subprocess.run(
                command,
                cwd=str(ROOT.parent),
                text=True,
                input="",
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
            stdout = completed.stdout.strip()
            if output_file.exists():
                final_message = output_file.read_text(encoding="utf-8").strip()
                if final_message:
                    stdout = final_message
            if completed.returncode != 0:
                raise RuntimeError(f"codex CLI failed: {completed.stderr.strip() or stdout}")
            return stdout

    def _prepare_gemini_home(self, isolated_root: Path) -> Path:
        source_home = Path(os.environ.get("GEMINI_CLI_HOME", Path.home() / ".gemini"))
        isolated_home = isolated_root / ".gemini_home"
        gemini_config_dir = isolated_home / ".gemini"
        gemini_config_dir.mkdir(parents=True, exist_ok=True)
        settings = {
            "security": {"auth": {"selectedType": "oauth-personal"}},
            "hooksConfig": {"enabled": False},
            "experimental": {"skills": False},
            "mcpServers": {},
            "hooks": {},
        }
        (gemini_config_dir / "settings.json").write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for filename in GEMINI_AUTH_FILES:
            source = source_home / filename
            if source.is_file():
                shutil.copy2(source, gemini_config_dir / filename)
        return isolated_home

    def _run_gemini_prompt(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="survey_agent_gemini_") as tmpdir:
            isolated_home = self._prepare_gemini_home(Path(tmpdir))
            env = os.environ.copy()
            for key in GEMINI_API_ENV_KEYS:
                env.pop(key, None)
            env["GEMINI_CLI_HOME"] = str(isolated_home)
            env["GEMINI_CLI_NO_RELAUNCH"] = "true"
            command = [
                self.binary,
                "--prompt",
                prompt,
                "--approval-mode",
                "plan",
                "--output-format",
                "text",
            ]
            completed = subprocess.run(
                command,
                cwd=str(ROOT.parent),
                text=True,
                input="",
                env=env,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
            stdout = re.sub(
                r"^MCP issues detected\. Run /mcp list for status\.\s*",
                "",
                completed.stdout.strip(),
            )
            if "Opening authentication page" in stdout or "Do you want to continue" in stdout:
                raise RuntimeError("gemini CLI is not authenticated in non-interactive mode.")
            if completed.returncode != 0:
                raise RuntimeError(f"gemini CLI failed: {completed.stderr.strip() or stdout}")
            return stdout

    def generate_json(self, prompt_path: Path, user_payload: Dict[str, Any], temperature: Optional[float] = None) -> Any:
        return extract_json(self.chat(load_text(prompt_path), user_payload, temperature=temperature))


def check_provider(provider: str, timeout_seconds: int = 45) -> Tuple[bool, str]:
    try:
        client = LLMClient(provider=provider)
        old_timeout = client.timeout
        client.timeout = min(old_timeout, timeout_seconds)
        result = client.generate_json(
            ROOT / "prompts/provider_healthcheck.md",
            {"provider": provider, "instruction": "Return JSON with ok=true."},
        )
        client.timeout = old_timeout
        if isinstance(result, dict) and result.get("ok") is True:
            return True, "ok"
        return False, f"unexpected healthcheck response: {result!r}"
    except Exception as exc:
        return False, str(exc)


def filter_available_providers(providers: List[str], log_fn=print) -> List[str]:
    available = []
    seen = set()
    for provider in providers:
        if provider in seen:
            continue
        seen.add(provider)
        ok, message = check_provider(provider)
        if ok:
            available.append(provider)
            log_fn(f"[Provider] {provider}: available")
        else:
            log_fn(f"[Provider] {provider}: unavailable ({message})")
    if not available:
        raise RuntimeError("No available LLM providers after health check.")
    return available

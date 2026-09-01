"""#4 depth baseline: same task, same prompt, two models. Real API, costs money.

Mirrors tests_e2e/conftest.py: load .env, run inside a temp sandbox so the
project is untouched, approve everything, and record what came back.
"""
import atexit, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

PROJECT = Path(r"D:\telegram_data_analyst_agent")
DATA = PROJECT / "data" / "NCHS_-_Leading_Causes_of_Death__United_States.csv"
MODEL = sys.argv[1]
OUT = Path(sys.argv[2])

for line in (PROJECT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
os.environ["OPENAI_MODEL"] = MODEL          # the override; .env is never written

sandbox = tempfile.mkdtemp(prefix=f"baseline-{MODEL.replace('.','_')}-")
atexit.register(shutil.rmtree, sandbox, ignore_errors=True)
os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
os.makedirs(os.path.join(sandbox, "output"), exist_ok=True)
shutil.copy(DATA, os.path.join(sandbox, "data", "deaths.csv"))
os.chdir(sandbox)
sys.path.insert(0, str(PROJECT / "src"))

from analyst.agent.builder import build_agent, build_model, build_checkpointer, build_summarizer
from analyst.agent.prompts import UPLOADED_FILE_TASK
from analyst.agent.runner import AgentRunner, run_to_completion, TooManyApprovals
from analyst.conversation.delivery import final_text
from analyst.plumbing.backend import create_backend

from langchain_openai import ChatOpenAI
# gpt-5.6-* reject function tools on /v1/chat/completions while reasoning_effort
# is in play; the Responses API is the fix that keeps reasoning switched on.
extra = {"use_responses_api": True} if MODEL.startswith("gpt-5") else {}
model = ChatOpenAI(model=MODEL, api_key=os.environ["OPENAI_API_KEY"], **extra)
assert model.model_name == MODEL, f"{model.model_name} != {MODEL}"
backend = create_backend(".")
agent = build_agent(model=model, checkpointer=build_checkpointer(os.path.join(sandbox, "cp.sqlite")),
                    target_backend=backend, summarizer=build_summarizer(model, backend),
                    python_path=sys.executable, output_dir="./output")

class Yes:
    def __init__(self): self.rounds = 0; self.decisions = []; self.log = []
    def ask(self, actions):
        self.rounds += 1
        self.log.append([a.name for a in actions])
        print(f"  [{MODEL}] round {self.rounds}: {[a.name for a in actions]}", flush=True)
        self.decisions = [{"type": "approve"} for _ in actions]

task = UPLOADED_FILE_TASK.format(file_path="./data/deaths.csv", output_dir="./output")
approver = Yes()
started = time.time()
error = None
try:
    result = run_to_completion(AgentRunner(agent), "baseline-1", task, approver, max_rounds=30)
except (TooManyApprovals, Exception) as exc:
    error = f"{type(exc).__name__}: {exc}"
    result = {}
elapsed = time.time() - started

prompt_toks = completion_toks = 0
tool_calls = []
for m in result.get("messages", []):
    u = getattr(m, "usage_metadata", None) or {}
    prompt_toks += u.get("input_tokens", 0); completion_toks += u.get("output_tokens", 0)
    for tc in (getattr(m, "tool_calls", None) or []):
        tool_calls.append(tc.get("name"))

images = sorted(p.name for p in Path("output").glob("*.png"))
scripts = sorted(p.name for p in Path("output").glob("*.py"))
report = {
    "model": MODEL, "error": error, "seconds": round(elapsed, 1),
    "approval_rounds": approver.rounds, "rounds_log": approver.log,
    "messages": len(result.get("messages", [])), "tool_calls": tool_calls,
    "input_tokens": prompt_toks, "output_tokens": completion_toks,
    "images": images, "scripts": scripts,
    "reply": final_text(result),
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / f"{MODEL}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
for name in images + scripts:
    shutil.copy(os.path.join("output", name), OUT / f"{MODEL}--{name}")
print(json.dumps({k: v for k, v in report.items() if k != "reply"}, indent=2))

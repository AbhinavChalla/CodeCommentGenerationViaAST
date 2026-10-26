# type: ignore
from flask import Flask, request, jsonify, send_from_directory
import ast
import logging
import time
import traceback
from google import genai
from google.genai import types
from backend.visualise import open_ast_pdf
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "DEBUG").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pycomment")

app = Flask(__name__, static_folder="../frontend")


TOKEN = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")

# gemini-2.5-flash now returns 404 "no longer available to new users",
# so the default is the model Google recommends as its replacement.
# Override with GEMINI_MODEL=... in .env if needed.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

log.debug("Startup: API key loaded = %s", bool(TOKEN))
log.debug("Startup: using model = %s", GEMINI_MODEL)
log.debug("Startup: static folder = %s", app.static_folder)

if not TOKEN:
    log.warning("No GEMINI_API_KEY / API_KEY found in environment. Gemini calls will fail.")

client = genai.Client(api_key=TOKEN)

CC_THRESHOLD = 15

SEC_CHECK_EVAL = (
    "1. Code Execution / Evaluation: eval(), exec(), compile(). "
    "(Risk: Remote Code Execution)"
)

SEC_CHECK_SHELL = (
    "2. OS Command Injection: os.system(), os.popen(), or subprocess calls "
    "configured with shell=True where inputs are unescaped. "
    "(Risk: Arbitrary shell command execution)"
)

SEC_CHECK_PICKLE = (
    "3. Insecure Deserialization: pickle.load(), pickle.loads(), or "
    "yaml.load() without SafeLoader. "
    "(Risk: Arbitrary code execution via serialized payloads)"
)

SEC_CHECK_PATH = (
    "4. Path Traversal & File Deletion: open(), os.remove(), or "
    "shutil.rmtree() utilizing unvalidated or user-controlled paths. "
    "(Risk: Unauthorized file read, write, or deletion)"
)

SEC_CHECK_IMPORT = (
    "5. Dynamic Imports / Reflection: __import__(), importlib.import_module(), "
    "or dynamic getattr() driven by untrusted input. "
    "(Risk: Unauthorized module loading)"
)


SECURITY_RULES_BLOCK = "\n".join([
    SEC_CHECK_EVAL,
    SEC_CHECK_SHELL,
    SEC_CHECK_PICKLE,
    SEC_CHECK_PATH,
    SEC_CHECK_IMPORT,
])


SYSTEM_PROMPT = (
    "You are a Python expert and application security reviewer. Add clear, helpful "
    "inline comments, docstrings, and complexity metrics to the code. Return ONLY the "
    "commented Python code with no extra explanation and no markdown fences.\n\n"
    "SECURITY AUDIT REQUIREMENTS:\n"
    "Carefully analyze the code for dangerous system calls, insecure functions, and "
    "vulnerabilities. If you detect any of the following risky patterns, you MUST add an "
    "immediate warning comment directly above the line (e.g., `# [SECURITY WARNING] ...`) "
    "explaining the risk and how to remediate it:\n"
    f"{SECURITY_RULES_BLOCK}\n\n"
    "If a function is called somewhere else in the code, explain where it is called and why. "
    "Ensure all cyclomatic complexity docstring instructions and summary blocks are strictly followed."
)


def calculate_cc(tree_or_code):
    """Calculate cyclomatic complexity from an AST node or source string."""
    if isinstance(tree_or_code, str):
        tree_or_code = ast.parse(tree_or_code)

    decision_nodes = (
        ast.If, ast.For, ast.While,
        ast.ExceptHandler, ast.With, ast.IfExp
    )

    complexity = 1
    for node in ast.walk(tree_or_code):
        if isinstance(node, decision_nodes):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            # Each extra operand in and/or adds a branch
            # e.g. a and b and c → 2 extra paths (len(values) - 1)
            complexity += len(node.values) - 1

    return complexity


def get_function_cc_info(source_code):
    """Compute per-function and file-level CC, return results + LLM context string."""
    tree = ast.parse(source_code)
    per_function = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cc = calculate_cc(node)
            per_function[node.name] = cc

    file_cc = calculate_cc(tree)

    lines = ["=== Cyclomatic Complexity Analysis ==="]
    lines.append(f"Overall file CC: {file_cc}")
    lines.append("")
    lines.append("Per-function CC:")
    for fname, cc in per_function.items():
        flag = f"  ABOVE THRESHOLD ({CC_THRESHOLD})" if cc > CC_THRESHOLD else "  BELOW THRESHOLD"
        lines.append(f"  {fname}: {cc}{flag}")

    lines.append("")
    lines.append(
        f"Threshold is {CC_THRESHOLD}. "
        "1. For any function whose per-function CC value is ABOVE this threshold, you MUST:\n"
        "Add the details about the complexity in the doc-string after writing about the function details but before mentioning the function parameters and return value details with:\n"
        f"    [CC Warning] Cyclomatic Complexity: <value> (threshold: {CC_THRESHOLD})\n"
        "     Add detailed AI-generated explanation of HOW the function code works in a greater detail\n"
        "     Also give concrete refactoring suggestions of how to reduce the overall code complexity\n"
        "2. For ALL functions whose per-function CC value is BELOW or EQUAL TO this threshold, just mention about the complexity in the doc-string after writing about the function details but before mentioning the function parameters and return value details:\n"
        "     [CC OK] Cyclomatic Complexity: <value>]\n"
        "3. At the very top of the file, add a summary comment block:\n"
        "     # ============================================================\n"
        "     # Cyclomatic Complexity Summary\n"
        "     # Overall file CC : <value>\n"
        "     # <func_name>     : <cc>  [WARNING: above threshold] (if applicable)\n"
        "     # ============================================================\n"
    )

    context_str = "\n".join(lines)
    return per_function, file_cc, context_str


def get_pretty_ast(source_code):
    """Return a pretty-printed (indented) AST string for the given source."""
    tree = ast.parse(source_code)
    return ast.dump(tree, indent=4)


def validate_code(source_code):
    try:
        ast.parse(source_code)
        return True
    except SyntaxError as e:
        return str(e)


def friendly_gemini_error(e):
    """Turn raw Gemini SDK errors into a short, actionable message."""
    msg = str(e)
    low = msg.lower()
    if "404" in msg or "not_found" in low or "no longer available" in low:
        return (
            f"Gemini model '{GEMINI_MODEL}' was not found or is no longer available. "
            "Set GEMINI_MODEL in your .env to a current model (e.g. gemini-3.8-flash). "
            f"Raw error: {msg}"
        )
    if "api key" in low or "401" in msg or "403" in msg or "permission" in low:
        return f"Gemini rejected the API key / permissions. Check GEMINI_API_KEY in .env. Raw error: {msg}"
    if "429" in msg or "quota" in low or "resource_exhausted" in low:
        return f"Gemini rate limit / quota exceeded. Try again shortly. Raw error: {msg}"
    return msg


@app.before_request
def log_request():
    log.debug("→ %s %s", request.method, request.path)


@app.after_request
def log_response(resp):
    log.debug("← %s %s -> %s", request.method, request.path, resp.status_code)
    return resp


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    log.debug("/generate: received %d chars, %d lines", len(code), code.count("\n") + 1 if code else 0)

    if not code:
        log.warning("/generate: empty code received")
        return jsonify({"error": "No code provided"}), 400

    valid = validate_code(code)
    if valid is not True:
        log.warning("/generate: syntax error in user code: %s", valid)
        return jsonify({"error": f"Syntax error in your code: {valid}"}), 400
    log.debug("/generate: syntax validation passed")

    try:
        per_function, file_cc, cc_context = get_function_cc_info(code)
        log.debug("/generate: CC analysis done (file_cc=%s, functions=%d)", file_cc, len(per_function))
    except Exception as e:
        log.error("/generate: CC analysis failed\n%s", traceback.format_exc())
        return jsonify({"error": f"CC analysis failed: {str(e)}"}), 500

    try:
        pretty_ast = get_pretty_ast(code)
        log.debug("/generate: AST generated (%d chars)", len(pretty_ast))
    except Exception as e:
        log.error("/generate: AST generation failed\n%s", traceback.format_exc())
        return jsonify({"error": f"AST generation failed: {str(e)}"}), 500

    try:
        user_prompt = (
            f"=== Pretty-printed AST ===\n\n{pretty_ast}\n\n"
            f"=== CC Analysis ===\n\n{cc_context}\n\n"
            "Using the AST and CC analysis above, produce the fully commented Python source code."
        )
        log.debug("/generate: calling Gemini model=%s, prompt=%d chars", GEMINI_MODEL, len(user_prompt))

        start = time.time()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                # Higher limit because Gemini models may spend tokens on "thinking"
                max_output_tokens=8192,
                temperature=0.2,
            ),
        )
        log.debug("/generate: Gemini responded in %.2fs", time.time() - start)

        # Extra debug info about the response
        try:
            cand = response.candidates[0] if response.candidates else None
            log.debug("/generate: finish_reason=%s", getattr(cand, "finish_reason", None))
            log.debug("/generate: usage=%s", getattr(response, "usage_metadata", None))
            if getattr(response, "prompt_feedback", None):
                log.debug("/generate: prompt_feedback=%s", response.prompt_feedback)
        except Exception:
            log.debug("/generate: could not read response metadata", exc_info=True)

        commented = response.text
        if not commented:
            log.error("/generate: Gemini returned empty text")
            return jsonify({"error": "Gemini returned an empty response (possibly blocked by safety filters or token limit)."}), 500

        commented = commented.strip()
        log.debug("/generate: received %d chars of commented code", len(commented))

        # Strip accidental markdown fences
        if commented.startswith("```"):
            log.debug("/generate: stripping markdown fences from output")
            lines = commented.splitlines()
            commented = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()

        return jsonify({
            "commented_code": commented,
            "cc_summary": {
                "file_cc": file_cc,
                "per_function": per_function,
                "threshold": CC_THRESHOLD,
            }
        })

    except Exception as e:
        log.error("/generate: Gemini call failed\n%s", traceback.format_exc())
        return jsonify({"error": friendly_gemini_error(e)}), 500


@app.route("/open_ast_pdf", methods=["POST"])
def open_ast_pdf_route():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    log.debug("/open_ast_pdf: received %d chars", len(code))
    if not code:
        return jsonify({"error": "No code provided"}), 400
    try:
        open_ast_pdf(code)
        log.debug("/open_ast_pdf: PDF generated and opened")
        return jsonify({"status": "ok"})
    except Exception as e:
        log.error("/open_ast_pdf: failed\n%s", traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
# type: ignore
from flask import Flask, request, jsonify, send_from_directory
import ast
from huggingface_hub import InferenceClient
from visualise import open_ast_pdf
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__, static_folder="../frontend")

TOKEN = os.getenv("API_KEY")
client = InferenceClient(api_key=TOKEN)

CC_THRESHOLD = 15


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


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    code = data.get("code", "").strip()

    if not code:
        return jsonify({"error": "No code provided"}), 400

    valid = validate_code(code)
    if valid is not True:
        return jsonify({"error": f"Syntax error in your code: {valid}"}), 400

    try:
        per_function, file_cc, cc_context = get_function_cc_info(code)
    except Exception as e:
        return jsonify({"error": f"CC analysis failed: {str(e)}"}), 500

    try:
        pretty_ast = get_pretty_ast(code)
    except Exception as e:
        return jsonify({"error": f"AST generation failed: {str(e)}"}), 500

    try:
        response = client.chat.completions.create(
            model="Qwen/Qwen3.5-35B-A3B",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Python expert. Add clear, helpful inline comments "
                        "and docstrings to the code. Return ONLY the commented Python "
                        "code with no extra explanation, no markdown fences."
                        "If there is a function which is called somewhere in the code, then tell about where it has been called and why."
                        "If the code is using the system calls and functions like os.system(), exec(), input() or eval()"
                        "In an unsafe way, then also include a comment about warning the user about potential security issues."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"=== Pretty-printed AST ===\n\n{pretty_ast}\n\n"
                        f"=== CC Analysis ===\n\n{cc_context}\n\n"
                        "Using the AST and CC analysis above, produce the fully commented Python source code."
                    ),
                },
            ],
            max_tokens=2000,
            temperature=0.2,
        )

        commented = response.choices[0].message.content

        # Strip accidental markdown fences
        if commented.startswith("```"):
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
        return jsonify({"error": str(e)}), 500


@app.route("/open_ast_pdf", methods=["POST"])
def open_ast_pdf_route():
    data = request.get_json()
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"error": "No code provided"}), 400
    try:
        open_ast_pdf(code)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
